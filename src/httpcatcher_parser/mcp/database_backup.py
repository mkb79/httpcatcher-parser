"""Database connection and schema management for MCP server."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

# Schema version for migrations
SCHEMA_VERSION = "1.0"

# SQL Schema definitions
SCHEMA_SQL = """
-- ==============================================================================
-- FILE TRACKING TABLE
-- ==============================================================================
CREATE TABLE IF NOT EXISTS session_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL UNIQUE,
    file_hash TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    last_modified INTEGER NOT NULL,
    indexed_at INTEGER NOT NULL,
    status TEXT DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_file_path ON session_files(file_path);
CREATE INDEX IF NOT EXISTS idx_file_hash ON session_files(file_hash);

-- ==============================================================================
-- HEADER/COOKIE KEY DICTIONARIES (Normalization)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS header_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    name_lower TEXT NOT NULL,
    usage_count INTEGER DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_header_key_name ON header_keys(name);
CREATE INDEX IF NOT EXISTS idx_header_key_lower ON header_keys(name_lower);

CREATE TABLE IF NOT EXISTS cookie_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    name_lower TEXT NOT NULL,
    usage_count INTEGER DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cookie_key_name ON cookie_keys(name);
CREATE INDEX IF NOT EXISTS idx_cookie_key_lower ON cookie_keys(name_lower);

-- ==============================================================================
-- REQUESTS TABLE (Main request/response data)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    original_request_id INTEGER NOT NULL,

    method TEXT,
    url TEXT,
    host TEXT,
    path TEXT,
    status_code INTEGER,

    req_timestamp INTEGER,
    resp_timestamp INTEGER,
    duration_ms INTEGER,

    connection_id INTEGER,
    port INTEGER,

    req_content_type TEXT,
    resp_content_type TEXT,
    resp_content_category TEXT,

    req_headers_json TEXT,
    resp_headers_json TEXT,
    req_cookies_json TEXT,
    resp_cookies_json TEXT,

    req_body_size INTEGER DEFAULT 0,
    resp_body_size INTEGER DEFAULT 0,
    req_body_offset INTEGER,
    resp_body_offset INTEGER,
    req_body_length INTEGER,
    resp_body_length INTEGER,
    req_body_blob BLOB,
    resp_body_blob BLOB,

    req_body_preview TEXT,
    resp_body_preview TEXT,

    FOREIGN KEY (file_id) REFERENCES session_files(id) ON DELETE CASCADE,
    UNIQUE(file_id, original_request_id)
);

CREATE INDEX IF NOT EXISTS idx_file_id ON requests(file_id);
CREATE INDEX IF NOT EXISTS idx_original_request_id ON requests(original_request_id);
CREATE INDEX IF NOT EXISTS idx_url ON requests(url);
CREATE INDEX IF NOT EXISTS idx_host ON requests(host);
CREATE INDEX IF NOT EXISTS idx_status ON requests(status_code);
CREATE INDEX IF NOT EXISTS idx_timestamp ON requests(req_timestamp);
CREATE INDEX IF NOT EXISTS idx_content_type ON requests(resp_content_type);
CREATE INDEX IF NOT EXISTS idx_content_category ON requests(resp_content_category);

-- ==============================================================================
-- HEADER/COOKIE DATA TABLES (with normalized keys)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS request_headers (
    request_id INTEGER NOT NULL,
    key_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
    FOREIGN KEY (key_id) REFERENCES header_keys(id)
);

CREATE INDEX IF NOT EXISTS idx_req_header_key ON request_headers(key_id);
CREATE INDEX IF NOT EXISTS idx_req_header_value ON request_headers(value);
CREATE INDEX IF NOT EXISTS idx_req_header_both ON request_headers(key_id, value);
CREATE INDEX IF NOT EXISTS idx_req_header_request ON request_headers(request_id);

CREATE TABLE IF NOT EXISTS response_headers (
    request_id INTEGER NOT NULL,
    key_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
    FOREIGN KEY (key_id) REFERENCES header_keys(id)
);

CREATE INDEX IF NOT EXISTS idx_resp_header_key ON response_headers(key_id);
CREATE INDEX IF NOT EXISTS idx_resp_header_value ON response_headers(value);
CREATE INDEX IF NOT EXISTS idx_resp_header_both ON response_headers(key_id, value);
CREATE INDEX IF NOT EXISTS idx_resp_header_request ON response_headers(request_id);

CREATE TABLE IF NOT EXISTS request_cookies (
    request_id INTEGER NOT NULL,
    key_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
    FOREIGN KEY (key_id) REFERENCES cookie_keys(id)
);

CREATE INDEX IF NOT EXISTS idx_req_cookie_key ON request_cookies(key_id);
CREATE INDEX IF NOT EXISTS idx_req_cookie_value ON request_cookies(value);
CREATE INDEX IF NOT EXISTS idx_req_cookie_both ON request_cookies(key_id, value);
CREATE INDEX IF NOT EXISTS idx_req_cookie_request ON request_cookies(request_id);

CREATE TABLE IF NOT EXISTS response_cookies (
    request_id INTEGER NOT NULL,
    key_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    domain TEXT,
    path TEXT,
    expires TEXT,
    http_only BOOLEAN DEFAULT 0,
    secure BOOLEAN DEFAULT 0,
    same_site TEXT,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
    FOREIGN KEY (key_id) REFERENCES cookie_keys(id)
);

CREATE INDEX IF NOT EXISTS idx_resp_cookie_key ON response_cookies(key_id);
CREATE INDEX IF NOT EXISTS idx_resp_cookie_value ON response_cookies(value);
CREATE INDEX IF NOT EXISTS idx_resp_cookie_both ON response_cookies(key_id, value);
CREATE INDEX IF NOT EXISTS idx_resp_cookie_request ON response_cookies(request_id);
CREATE INDEX IF NOT EXISTS idx_resp_cookie_secure ON response_cookies(secure);
CREATE INDEX IF NOT EXISTS idx_resp_cookie_httponly ON response_cookies(http_only);

-- ==============================================================================
-- QUERY PARAMETERS TABLE
-- ==============================================================================
CREATE TABLE IF NOT EXISTS query_params (
    request_id INTEGER,
    param_name TEXT,
    param_value TEXT,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_query_param ON query_params(param_name, param_value);

-- ==============================================================================
-- METADATA TABLE
-- ==============================================================================
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# FTS5 full-text search (optional, created separately)
FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS body_search USING fts5(
    request_id UNINDEXED,
    req_body_preview,
    resp_body_preview,
    content='requests',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS requests_ai AFTER INSERT ON requests BEGIN
    INSERT INTO body_search(rowid, request_id, req_body_preview, resp_body_preview)
    VALUES (new.id, new.original_request_id, new.req_body_preview, new.resp_body_preview);
END;

CREATE TRIGGER IF NOT EXISTS requests_ad AFTER DELETE ON requests BEGIN
    DELETE FROM body_search WHERE rowid = old.id;
END;
"""

# Performance PRAGMA settings
PRAGMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA cache_size = -128000;
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 536870912;
PRAGMA page_size = 8192;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA wal_autocheckpoint = 1000;
"""


class Database:
    """SQLite database connection and schema management."""

    def __init__(self, db_path: str | Path):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Use ":memory:" for in-memory DB.
        """
        self.db_path = Path(db_path) if db_path != ":memory:" else db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Get database connection, creating if needed."""
        if self._conn is None:
            self._conn = self._create_connection()
        return self._conn

    def _create_connection(self) -> sqlite3.Connection:
        """Create and configure database connection."""
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,  # Allow multi-threaded access
            timeout=30.0,  # Wait up to 30 seconds for locks
        )
        conn.row_factory = sqlite3.Row  # Access columns by name

        # Apply performance settings
        conn.executescript(PRAGMA_SQL)

        return conn

    def initialize(self, enable_fts: bool = True) -> None:
        """Initialize database schema.

        Args:
            enable_fts: Whether to enable full-text search (FTS5).
        """
        # Create all tables and indices
        self.conn.executescript(SCHEMA_SQL)

        # Create FTS if requested
        if enable_fts:
            try:
                self.conn.executescript(FTS_SQL)
            except sqlite3.OperationalError:
                # FTS5 not available, continue without it
                pass

        # Initialize metadata
        cursor = self.conn.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        )
        if cursor.fetchone() is None:
            self.conn.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                ("schema_version", SCHEMA_VERSION),
            )
            import time

            created_at = (
                int(time.time())
                if self.db_path == ":memory:"
                else int(Path(self.db_path).stat().st_ctime)
            )
            self.conn.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                ("created_at", str(created_at)),
            )

        self.conn.commit()

    def reset(self) -> None:
        """Reset database (delete all data, keep schema)."""
        # Drop FTS5 tables and triggers first (they may reference old schema)
        try:
            self.conn.execute("DROP TRIGGER IF EXISTS requests_ai")
            self.conn.execute("DROP TRIGGER IF EXISTS requests_ad")
            self.conn.execute("DROP TABLE IF EXISTS body_search")
        except Exception:
            pass

        # Get all table names except metadata
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('metadata', 'sqlite_sequence')"
        )
        tables = [row[0] for row in cursor.fetchall()]

        # Delete all data
        for table in tables:
            self.conn.execute(f"DELETE FROM {table}")

        self.conn.commit()

        # Recreate FTS5 if it was enabled
        try:
            self.conn.executescript(FTS_SQL)
        except Exception:
            pass  # FTS5 not available or not needed

    def vacuum(self) -> None:
        """Optimize database (VACUUM)."""
        self.conn.execute("VACUUM")

    def analyze(self) -> None:
        """Update query planner statistics."""
        self.conn.execute("ANALYZE")

    def get_stats(self) -> dict:
        """Get database statistics.

        Returns:
            Dict with various statistics about the database.
        """
        stats = {}

        # Total requests
        cursor = self.conn.execute("SELECT COUNT(*) FROM requests")
        stats["total_requests"] = cursor.fetchone()[0]

        # Total files
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM session_files WHERE status = 'active'"
        )
        stats["total_files"] = cursor.fetchone()[0]

        # Total header keys
        cursor = self.conn.execute("SELECT COUNT(*) FROM header_keys")
        stats["total_header_keys"] = cursor.fetchone()[0]

        # Total cookie keys
        cursor = self.conn.execute("SELECT COUNT(*) FROM cookie_keys")
        stats["total_cookie_keys"] = cursor.fetchone()[0]

        return stats

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
