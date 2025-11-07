"""Async database connection and schema management for MCP server."""

from __future__ import annotations

import aiosqlite
from pathlib import Path
from typing import Optional

# Schema version for migrations
SCHEMA_VERSION = "1.0"

# Import schema constants from backup
from .database_backup import SCHEMA_SQL, FTS_SQL, PRAGMA_SQL


class Database:
    """Async SQLite database connection and schema management."""

    def __init__(self, db_path: str | Path):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Use ":memory:" for in-memory DB.
        """
        self.db_path = Path(db_path) if db_path != ":memory:" else db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> aiosqlite.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = await self._create_connection()
        return self._conn

    async def _create_connection(self) -> aiosqlite.Connection:
        """Create and configure database connection."""
        conn = await aiosqlite.connect(
            str(self.db_path),
            timeout=30.0  # Wait up to 30 seconds for locks
        )
        conn.row_factory = aiosqlite.Row  # Access columns by name

        # Apply performance settings
        await conn.executescript(PRAGMA_SQL)

        return conn

    async def initialize(self, enable_fts: bool = True) -> None:
        """Initialize database schema.

        Args:
            enable_fts: Whether to enable full-text search (FTS5).
        """
        conn = await self.connect()

        # Create all tables and indices
        await conn.executescript(SCHEMA_SQL)

        # Create FTS if requested
        if enable_fts:
            try:
                await conn.executescript(FTS_SQL)
            except aiosqlite.OperationalError:
                # FTS5 not available, continue without it
                pass

        # Initialize metadata
        cursor = await conn.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        )
        if await cursor.fetchone() is None:
            await conn.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                ("schema_version", SCHEMA_VERSION)
            )
            import time
            created_at = int(time.time()) if self.db_path == ":memory:" else int(Path(self.db_path).stat().st_ctime)
            await conn.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                ("created_at", str(created_at))
            )

        await conn.commit()

    async def reset(self) -> None:
        """Reset database (delete all data, keep schema)."""
        conn = await self.connect()

        # Drop FTS5 tables and triggers first
        try:
            await conn.execute("DROP TRIGGER IF EXISTS requests_ai")
            await conn.execute("DROP TRIGGER IF EXISTS requests_ad")
            await conn.execute("DROP TABLE IF EXISTS body_search")
        except Exception:
            pass

        # Get all table names except metadata
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('metadata', 'sqlite_sequence')"
        )
        tables = [row[0] async for row in cursor]

        # Delete all data
        for table in tables:
            await conn.execute(f"DELETE FROM {table}")

        await conn.commit()

        # Recreate FTS5
        try:
            await conn.executescript(FTS_SQL)
        except Exception:
            pass

    async def vacuum(self) -> None:
        """Optimize database (VACUUM)."""
        conn = await self.connect()
        await conn.execute("VACUUM")

    async def analyze(self) -> None:
        """Update query planner statistics."""
        conn = await self.connect()
        await conn.execute("ANALYZE")

    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self):
        """Context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        await self.close()
