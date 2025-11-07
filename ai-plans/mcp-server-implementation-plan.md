# MCP Server Implementation Plan

**Date:** 2025-01-07
**Status:** Planning Phase
**Target:** Add optional MCP server to httpcatcher-parser package

---

## Overview

Extend the `httpcatcher-parser` package with an optional MCP (Model Context Protocol) server that enables efficient querying and analysis of HTTP Catcher session files through Claude Desktop.

---

## Architecture Decisions

### 1. Language & Integration
- **Language:** Python (reuse existing parser code)
- **Integration:** Optional feature via `[mcp]` extras in pyproject.toml
- **Package Structure:** Single package with submodule `httpcatcher_parser.mcp`

### 2. Storage & Indexing
- **Strategy:** SQLite-based index with normalized schema
- **Location:** `~/.http_catcher/` (configurable)
- **File Tracking:** SHA256 hashing for change detection
- **Body Storage:** Lazy loading via file offsets (not stored in DB)

### 3. CLI Design
- **Single command:** `hc-mcp` (replaces multiple separate commands)
- **Subcommands:** init, server, db, files, config
- **Server start:** `hc-mcp server` (not separate binary)

---

## Database Schema

### Core Tables

#### `session_files` - File Tracking
```sql
CREATE TABLE session_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL UNIQUE,
    file_hash TEXT NOT NULL,           -- SHA256 for change detection
    file_size INTEGER NOT NULL,
    last_modified INTEGER NOT NULL,    -- Unix timestamp
    indexed_at INTEGER NOT NULL,
    status TEXT DEFAULT 'active'       -- 'active', 'deleted', 'modified'
);
```

#### `requests` - Main Request/Response Data
```sql
CREATE TABLE requests (
    id INTEGER PRIMARY KEY,            -- request_id from session
    file_id INTEGER NOT NULL,

    -- Basic metadata
    method TEXT,
    url TEXT,
    host TEXT,
    path TEXT,
    status_code INTEGER,

    -- Timestamps (ms since epoch)
    req_timestamp INTEGER,
    resp_timestamp INTEGER,
    duration_ms INTEGER,

    -- Connection info
    connection_id INTEGER,
    port INTEGER,

    -- Content types
    req_content_type TEXT,
    resp_content_type TEXT,
    resp_content_category TEXT,        -- 'json', 'image', 'media', etc.

    -- Headers/Cookies (JSON for quick detail retrieval)
    req_headers_json TEXT,
    resp_headers_json TEXT,
    req_cookies_json TEXT,
    resp_cookies_json TEXT,

    -- Body metadata (NOT the content itself!)
    req_body_size INTEGER DEFAULT 0,
    resp_body_size INTEGER DEFAULT 0,
    req_body_offset INTEGER,           -- File position for lazy load
    resp_body_offset INTEGER,
    req_body_length INTEGER,
    resp_body_length INTEGER,

    -- Full-text search preview (first 500 chars)
    req_body_preview TEXT,
    resp_body_preview TEXT,

    FOREIGN KEY (file_id) REFERENCES session_files(id) ON DELETE CASCADE
);
```

### Normalized Key Tables (Performance Optimization)

#### `header_keys` - Header Name Dictionary
```sql
CREATE TABLE header_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,         -- e.g. "Content-Type"
    name_lower TEXT NOT NULL,          -- Lowercase for case-insensitive search
    usage_count INTEGER DEFAULT 0      -- Statistics
);

CREATE UNIQUE INDEX idx_header_key_name ON header_keys(name);
CREATE INDEX idx_header_key_lower ON header_keys(name_lower);
```

#### `cookie_keys` - Cookie Name Dictionary
```sql
CREATE TABLE cookie_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    name_lower TEXT NOT NULL,
    usage_count INTEGER DEFAULT 0
);

CREATE UNIQUE INDEX idx_cookie_key_name ON cookie_keys(name);
CREATE INDEX idx_cookie_key_lower ON cookie_keys(name_lower);
```

### Header/Cookie Data Tables

#### `request_headers` - With Normalized Keys
```sql
CREATE TABLE request_headers (
    request_id INTEGER NOT NULL,
    key_id INTEGER NOT NULL,           -- FK to header_keys
    value TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
    FOREIGN KEY (key_id) REFERENCES header_keys(id)
);

CREATE INDEX idx_req_header_key ON request_headers(key_id);
CREATE INDEX idx_req_header_value ON request_headers(value);
CREATE INDEX idx_req_header_both ON request_headers(key_id, value);
CREATE INDEX idx_req_header_request ON request_headers(request_id);
```

#### `response_headers`
```sql
CREATE TABLE response_headers (
    request_id INTEGER NOT NULL,
    key_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
    FOREIGN KEY (key_id) REFERENCES header_keys(id)
);

CREATE INDEX idx_resp_header_key ON response_headers(key_id);
CREATE INDEX idx_resp_header_value ON response_headers(value);
CREATE INDEX idx_resp_header_both ON response_headers(key_id, value);
CREATE INDEX idx_resp_header_request ON response_headers(request_id);
```

#### `request_cookies`
```sql
CREATE TABLE request_cookies (
    request_id INTEGER NOT NULL,
    key_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
    FOREIGN KEY (key_id) REFERENCES cookie_keys(id)
);

CREATE INDEX idx_req_cookie_key ON request_cookies(key_id);
CREATE INDEX idx_req_cookie_value ON request_cookies(value);
CREATE INDEX idx_req_cookie_both ON request_cookies(key_id, value);
CREATE INDEX idx_req_cookie_request ON request_cookies(request_id);
```

#### `response_cookies`
```sql
CREATE TABLE response_cookies (
    request_id INTEGER NOT NULL,
    key_id INTEGER NOT NULL,
    value TEXT NOT NULL,
    -- Optional cookie attributes
    domain TEXT,
    path TEXT,
    expires TEXT,
    http_only BOOLEAN DEFAULT 0,
    secure BOOLEAN DEFAULT 0,
    same_site TEXT,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
    FOREIGN KEY (key_id) REFERENCES cookie_keys(id)
);

CREATE INDEX idx_resp_cookie_key ON response_cookies(key_id);
CREATE INDEX idx_resp_cookie_value ON response_cookies(value);
CREATE INDEX idx_resp_cookie_both ON response_cookies(key_id, value);
CREATE INDEX idx_resp_cookie_request ON response_cookies(request_id);
CREATE INDEX idx_resp_cookie_secure ON response_cookies(secure);
CREATE INDEX idx_resp_cookie_httponly ON response_cookies(http_only);
```

### Full-Text Search (Optional)

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS body_search USING fts5(
    request_id UNINDEXED,
    req_body_preview,
    resp_body_preview,
    content='requests',
    content_rowid='id'
);
```

### Query Parameters Table

```sql
CREATE TABLE query_params (
    request_id INTEGER,
    param_name TEXT,
    param_value TEXT,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE
);

CREATE INDEX idx_query_param ON query_params(param_name, param_value);
```

### Metadata Table

```sql
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

INSERT INTO metadata VALUES ('schema_version', '1.0');
INSERT INTO metadata VALUES ('created_at', strftime('%s', 'now'));
```

### Performance PRAGMA Settings

```sql
PRAGMA journal_mode = WAL;          -- Write-Ahead Logging
PRAGMA synchronous = NORMAL;        -- Balance safety/speed
PRAGMA cache_size = -64000;         -- 64MB cache
PRAGMA temp_store = MEMORY;         -- Temp tables in RAM
PRAGMA mmap_size = 268435456;       -- 256MB mmap
```

---

## File Structure

```
httpcatcher-parser/
├── src/httpcatcher_parser/
│   ├── __init__.py
│   ├── hc_parser.py              # Existing
│   ├── hc_har.py                 # Existing
│   └── mcp/                      # NEW
│       ├── __init__.py
│       ├── cli.py                # Main CLI entry point
│       ├── server.py             # MCP server implementation
│       ├── manage.py             # Management commands (db, files)
│       ├── database.py           # SQLite connection & schema
│       ├── indexer.py            # Session file → DB indexing
│       ├── file_tracker.py       # File change detection & sync
│       ├── query_engine.py       # Filter logic & SQL builder
│       ├── detail_fetcher.py     # Partial response builder
│       ├── content_analyzer.py   # Content-Type categorization
│       └── tools/                # MCP tool implementations
│           ├── __init__.py
│           ├── search.py         # search_requests
│           ├── details.py        # get_request_details
│           ├── index.py          # index_file/directory
│           ├── sync.py           # sync_files
│           └── stats.py          # get_stats, get_available_keys
│
├── ai-plans/                     # NEW: Implementation plans
│   └── mcp-server-implementation-plan.md
├── pyproject.toml                # Updated with [mcp] extras
└── README.md                     # Updated with MCP docs
```

---

## MCP Tools

### Tool 1: `index_file`
**Description:** Index a single HTTP Catcher session file into the database

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "file_path": {"type": "string"},
    "force_reindex": {"type": "boolean", "default": false}
  },
  "required": ["file_path"]
}
```

**Behavior:**
- Check if file already indexed (via file_hash)
- If force_reindex=true: delete old entries, re-index
- Return: {indexed: bool, requests_added: int, file_id: int}

---

### Tool 2: `index_directory`
**Description:** Index all .session files in a directory recursively

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "directory_path": {"type": "string"},
    "recursive": {"type": "boolean", "default": true},
    "pattern": {"type": "string", "default": "*.session"}
  },
  "required": ["directory_path"]
}
```

**Behavior:**
- Find all session files
- Index only new/changed files (hash comparison)
- Return: {total_files: int, indexed: int, skipped: int, errors: list}

---

### Tool 3: `sync_files`
**Description:** Synchronize database with filesystem (add new, remove deleted files)

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "base_paths": {
      "type": "array",
      "items": {"type": "string"}
    }
  },
  "required": ["base_paths"]
}
```

**Behavior:**
1. Check all session_files entries
2. Delete DB entries for non-existent files (CASCADE → deletes requests)
3. Scan base_paths for new files
4. Index new files
5. Return: {added: int, removed: int, updated: int}

---

### Tool 4: `search_requests`
**Description:** Search requests with multiple filter criteria

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "time_range": {
      "type": "object",
      "properties": {
        "start": {"type": "integer"},
        "end": {"type": "integer"}
      }
    },
    "url_contains": {"type": "string"},
    "url_regex": {"type": "string"},
    "host": {"type": "string"},
    "path_contains": {"type": "string"},

    "request_headers": {
      "type": "object",
      "properties": {
        "key": {"type": "string"},
        "value": {"type": "string"},
        "match_mode": {
          "enum": ["exact", "contains", "starts_with", "ends_with", "regex"],
          "default": "contains"
        }
      }
    },
    "response_headers": {
      "type": "object",
      "properties": {
        "key": {"type": "string"},
        "value": {"type": "string"},
        "match_mode": {
          "enum": ["exact", "contains", "starts_with", "ends_with", "regex"],
          "default": "contains"
        }
      }
    },

    "request_cookies": {
      "type": "object",
      "properties": {
        "key": {"type": "string"},
        "value": {"type": "string"},
        "match_mode": {
          "enum": ["exact", "contains", "starts_with", "ends_with"],
          "default": "contains"
        }
      }
    },
    "response_cookies": {
      "type": "object",
      "properties": {
        "key": {"type": "string"},
        "value": {"type": "string"},
        "match_mode": {
          "enum": ["exact", "contains", "starts_with", "ends_with"],
          "default": "contains"
        },
        "http_only": {"type": "boolean"},
        "secure": {"type": "boolean"}
      }
    },

    "request_body_contains": {"type": "string"},
    "response_body_contains": {"type": "string"},

    "status_codes": {
      "type": "array",
      "items": {"type": "integer"}
    },
    "status_range": {
      "type": "object",
      "properties": {
        "min": {"type": "integer"},
        "max": {"type": "integer"}
      }
    },

    "content_types": {
      "type": "array",
      "items": {
        "enum": ["json", "image", "media", "websocket",
                "html", "css", "javascript", "font", "other"]
      }
    },

    "limit": {"type": "integer", "default": 100},
    "offset": {"type": "integer", "default": 0}
  }
}
```

**Response:**
```json
{
  "total": 1523,
  "results": [
    {
      "id": 12345,
      "method": "POST",
      "url": "https://api.example.com/users",
      "status": 201,
      "timestamp": 1704067200000,
      "duration_ms": 245,
      "file_path": "/path/to/session.file",
      "content_type": "json",
      "summary": {
        "request": {"headers": 15, "cookies": 2, "body_size": 512},
        "response": {"headers": 12, "cookies": 3, "body_size": 2048}
      }
    }
  ]
}
```

---

### Tool 5: `get_request_details`
**Description:** Get detailed information for a specific request

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "request_id": {"type": "integer"},
    "include": {
      "type": "array",
      "items": {
        "enum": ["request_headers", "request_cookies", "request_body",
                "response_headers", "response_cookies", "response_body",
                "all"]
      },
      "default": ["all"]
    },
    "decompress_body": {"type": "boolean", "default": true}
  },
  "required": ["request_id"]
}
```

**Behavior:**
- Load only requested parts from DB
- Bodies loaded lazily from original file (via mmap)
- If decompress_body=true: decompress gzip/brotli
- Return: structured JSON with requested fields

---

### Tool 6: `get_stats`
**Description:** Get database statistics

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "group_by": {
      "enum": ["file", "host", "status", "content_type", "method"],
      "default": "file"
    }
  }
}
```

**Response:**
```json
{
  "total_files": 42,
  "total_requests": 15234,
  "by_file": [
    {"file": "/path/to/session1.file", "requests": 512, "size_mb": 4.5}
  ],
  "by_status": {
    "200": 12000,
    "404": 234,
    "500": 12
  }
}
```

---

### Tool 7: `get_available_keys`
**Description:** Get all available header/cookie keys with usage statistics

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "type": {"enum": ["header_keys", "cookie_keys"]},
    "min_usage": {"type": "integer", "default": 1},
    "sort_by": {"enum": ["name", "usage_count"], "default": "usage_count"}
  }
}
```

**Response:**
```json
{
  "keys": [
    {"name": "Content-Type", "usage_count": 1523456},
    {"name": "Authorization", "usage_count": 892341}
  ]
}
```

---

### Tool 8: `autocomplete_key`
**Description:** Get key suggestions for autocomplete

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "prefix": {"type": "string"},
    "type": {"enum": ["header", "cookie"]},
    "limit": {"type": "integer", "default": 10}
  }
}
```

---

## CLI Commands

### Management: `hc-mcp`

```bash
# Initialization
hc-mcp init                              # Setup directory & database

# Server
hc-mcp server                            # Start MCP server (stdio)
hc-mcp server --log-level debug          # Debug mode
hc-mcp server --db-path /custom/path.db  # Custom DB

# Database Management
hc-mcp db status                         # Show statistics
hc-mcp db vacuum                         # Optimize (VACUUM + ANALYZE)
hc-mcp db stats                          # Detailed statistics
hc-mcp db reset                          # Delete all data
hc-mcp db reset --confirm                # Skip confirmation
hc-mcp db migrate                        # Run schema migrations

# File Management
hc-mcp files list                        # List all files
hc-mcp files list --format json          # JSON output
hc-mcp files list --sort requests        # Sort by request count

hc-mcp files add <path>                  # Add file (copy)
hc-mcp files add <path> --move           # Move file
hc-mcp files add <path> --link           # Symlink file
hc-mcp files add <path> --name custom    # Custom name

hc-mcp files add-dir <dir>               # Add directory
hc-mcp files add-dir <dir> --recursive   # Recursive scan
hc-mcp files add-dir <dir> --pattern "*.hc"  # Custom pattern

hc-mcp files remove <id>                 # Remove from DB
hc-mcp files remove <id> --delete-file   # Remove + delete file

hc-mcp files info <id>                   # Show details
hc-mcp files reindex <id>                # Re-index file
hc-mcp files reindex --all               # Re-index all

hc-mcp files check                       # Consistency check
hc-mcp files check --fix                 # Auto-fix issues

# Configuration
hc-mcp config get                        # Show all config
hc-mcp config get data_dir               # Show specific key
hc-mcp config set log_level debug        # Update config
```

---

## Implementation Milestones

### Milestone 1: Foundation (Days 1-2)
**Goal:** Project structure and database setup

**Tasks:**
- [ ] Update `pyproject.toml` with [mcp] extras
- [ ] Create `src/httpcatcher_parser/mcp/` directory structure
- [ ] Implement `database.py`:
  - Schema creation with all tables
  - PRAGMA settings
  - Connection management
  - Schema versioning
- [ ] Implement basic `cli.py`:
  - Argument parser setup
  - Command routing
  - `hc-mcp init` command
- [ ] Write tests for database initialization

**Deliverables:**
- `hc-mcp init` creates directory structure and empty database
- Schema validation tests pass

---

### Milestone 2: Indexer (Days 3-4)
**Goal:** Parse session files and populate database

**Tasks:**
- [ ] Implement `content_analyzer.py`:
  - Content-Type categorization (json, image, media, etc.)
  - MIME type parsing
- [ ] Implement `indexer.py`:
  - `parse_session_to_db(file_path, db_conn)`
  - Header/Cookie parsing → normalized keys
  - Body offset calculation
  - Request/Response aggregation
  - Batch insert optimization
- [ ] Implement `file_tracker.py`:
  - SHA256 hashing
  - File metadata extraction
  - `add_file()`, `remove_file()`
- [ ] Implement `hc-mcp files add` command
- [ ] Write indexer tests with sample session files

**Deliverables:**
- Single files can be indexed successfully
- All data correctly stored in normalized schema
- File tracking with hashes works

---

### Milestone 3: File Sync (Days 5-6)
**Goal:** Directory scanning and synchronization

**Tasks:**
- [ ] Extend `file_tracker.py`:
  - `scan_directory(path, pattern, recursive)`
  - `detect_changes()` → hash comparison
  - `sync_database()` → add/remove logic
  - `check_consistency()` → orphaned/untracked detection
  - `fix_issues()` → auto-repair
- [ ] Implement CLI commands:
  - `hc-mcp files add-dir`
  - `hc-mcp files list`
  - `hc-mcp files remove`
  - `hc-mcp files check`
  - `hc-mcp files info`
  - `hc-mcp files reindex`
- [ ] Implement `sync_files` MCP tool
- [ ] Write sync tests

**Deliverables:**
- Directories can be batch-indexed
- Sync detects and fixes inconsistencies
- All file management commands functional

---

### Milestone 4: Query Engine (Days 7-9)
**Goal:** Implement flexible search with all filters

**Tasks:**
- [ ] Implement `query_engine.py`:
  - SQL builder for all filter types
  - Header/Cookie key-based filters (with normalization)
  - Value pattern matching (exact, contains, starts_with, ends_with, regex)
  - Time range filters
  - Status code filters
  - Content-Type filters
  - Body text search (FTS5 integration)
  - Pagination support
- [ ] Implement `search_requests` MCP tool
- [ ] Implement `get_stats` MCP tool
- [ ] Implement `get_available_keys` MCP tool
- [ ] Implement `autocomplete_key` MCP tool
- [ ] Write comprehensive query tests
- [ ] Performance benchmarking

**Deliverables:**
- All filter combinations work correctly
- Query performance meets targets (<100ms for most queries)
- Statistics tools functional

---

### Milestone 5: Detail Fetcher (Days 10-11)
**Goal:** Efficient partial data retrieval

**Tasks:**
- [ ] Implement `detail_fetcher.py`:
  - `load_body_from_file(file_path, offset, length)` with mmap
  - Selective field loading (request-only, response-only, etc.)
  - Body decompression support (gzip, brotli, deflate)
  - Header/Cookie detail formatting
- [ ] Implement `get_request_details` MCP tool
- [ ] Optimize body loading performance
- [ ] Write detail fetcher tests

**Deliverables:**
- Partial data retrieval works efficiently
- Body loading via mmap is fast (<10ms)
- Decompression works for all supported formats

---

### Milestone 6: MCP Server (Days 12-13)
**Goal:** Complete MCP server implementation

**Tasks:**
- [ ] Implement `server.py`:
  - MCP server setup with stdio transport
  - Tool registration
  - Error handling
  - Logging configuration
- [ ] Implement `tools/` package:
  - Register all tools with MCP server
  - Input validation
  - Response formatting
- [ ] Implement `hc-mcp server` command
- [ ] Write server integration tests
- [ ] Test with Claude Desktop

**Deliverables:**
- Server starts and responds via stdio
- All tools accessible via MCP
- Claude Desktop integration works

---

### Milestone 7: Database Management (Days 14-15)
**Goal:** Complete database management commands

**Tasks:**
- [ ] Implement DB commands:
  - `hc-mcp db status`
  - `hc-mcp db vacuum`
  - `hc-mcp db stats`
  - `hc-mcp db reset`
  - `hc-mcp db migrate`
- [ ] Implement config commands:
  - `hc-mcp config get`
  - `hc-mcp config set`
- [ ] Create default config.json
- [ ] Write DB management tests

**Deliverables:**
- All DB management commands work
- Configuration system functional
- Migration system ready for future updates

---

### Milestone 8: Documentation & Polish (Days 16-17)
**Goal:** Production-ready release

**Tasks:**
- [ ] Update README.md:
  - Installation instructions
  - Quick start guide
  - Command reference
  - Claude Desktop configuration
  - Example queries
- [ ] Create comprehensive docs:
  - Architecture documentation
  - Database schema documentation
  - MCP tool reference
  - Performance tuning guide
- [ ] Add inline code documentation
- [ ] Create example session files for testing
- [ ] Performance optimization pass
- [ ] Error message improvements

**Deliverables:**
- Complete documentation
- Production-ready code quality
- Example files for users

---

### Milestone 9: Testing & Release (Days 18-19)
**Goal:** Comprehensive testing and release

**Tasks:**
- [ ] Unit tests for all modules
- [ ] Integration tests for complete workflows
- [ ] Performance benchmarks
- [ ] Edge case testing (empty files, corrupted files, etc.)
- [ ] Memory leak testing
- [ ] Large dataset testing (10000+ requests)
- [ ] CI/CD setup (if applicable)
- [ ] Tag release version

**Deliverables:**
- >90% test coverage
- All tests passing
- Performance benchmarks documented
- Ready for production use

---

## Performance Targets

| Operation | Target Time | Notes |
|-----------|-------------|-------|
| Index 1000 requests | <5 seconds | Initial indexing |
| URL search | <50ms | Simple filter |
| Header key search | <10ms | With normalization |
| Header key+value search | <15ms | With normalization |
| Body text search | <200ms | With FTS5 |
| Detail fetch | <10ms | Via mmap |
| Directory sync (100 files) | <30 seconds | Parallel processing |
| Database vacuum (10GB) | <60 seconds | VACUUM operation |

---

## Expected Database Sizes

| Dataset | DB Size | Notes |
|---------|---------|-------|
| 1,000 requests | ~5 MB | Average |
| 10,000 requests | ~50 MB | Medium |
| 100,000 requests | ~500 MB | Large |
| 1,000,000 requests | ~5 GB | Very large |

**Space savings:** 25% compared to non-normalized schema (storing full header/cookie names)

---

## Content-Type Categories

```python
CONTENT_TYPE_MAP = {
    'json': [
        'application/json',
        'application/ld+json',
        'application/vnd.api+json'
    ],
    'image': ['image/'],
    'media': ['video/', 'audio/'],
    'websocket': ['application/websocket'],
    'html': ['text/html'],
    'css': ['text/css'],
    'javascript': [
        'application/javascript',
        'text/javascript',
        'application/x-javascript'
    ],
    'font': ['font/', 'application/font'],
}
```

---

## Example Workflows

### Workflow 1: Initial Setup
```bash
# Install with MCP support
uv tool install "git+https://github.com/mkb79/httpcatcher-parser.git#egg=httpcatcher-parser[mcp]"

# Initialize
hc-mcp init

# Add session files
hc-mcp files add-dir ~/http-catcher-exports/ --recursive

# Check status
hc-mcp db status
hc-mcp files list
```

### Workflow 2: Using with Claude Desktop
```json
// claude_desktop_config.json
{
  "mcpServers": {
    "httpcatcher": {
      "command": "hc-mcp",
      "args": ["server"]
    }
  }
}
```

**Claude interaction:**
```
User: "Find all failed POST requests to /api/users"
Claude: [Uses search_requests with filters: method="POST", path="/api/users", status_range={min:400,max:599}]

User: "Show me the full response for request 12345"
Claude: [Uses get_request_details with request_id=12345, include=["response_body","response_headers"]]

User: "What are the most common response headers?"
Claude: [Uses get_available_keys with type="header_keys", sort_by="usage_count"]
```

### Workflow 3: Maintenance
```bash
# Check for issues
hc-mcp files check

# Fix issues automatically
hc-mcp files check --fix

# Optimize database
hc-mcp db vacuum

# View statistics
hc-mcp db stats
```

---

## Dependencies

### Core (always required)
- `brotli>=1.1.0` - Decompression support

### MCP Extra ([mcp])
- `mcp>=0.9.0` - MCP SDK

### Development ([dev])
- `pytest>=7.0`
- `pytest-asyncio>=0.21.0`
- `ruff>=0.1.0`

---

## Configuration File

**Location:** `~/.http_catcher/config.json`

```json
{
  "data_dir": "~/.http_catcher",
  "db_path": "~/.http_catcher/index.db",
  "sessions_dir": "~/.http_catcher/sessions",
  "auto_index": true,
  "max_file_size_mb": 500,
  "log_level": "info",
  "server": {
    "port": null,
    "host": null
  },
  "indexing": {
    "batch_size": 1000,
    "parallel_workers": 4
  },
  "query": {
    "default_limit": 100,
    "max_limit": 10000
  }
}
```

---

## Security Considerations

1. **SQL Injection:** All queries use parameterized statements
2. **Path Traversal:** All file paths validated and normalized
3. **Resource Limits:** Configurable limits for file size and query results
4. **Permissions:** Database and files use restrictive permissions (0600)

---

## Future Enhancements

- [ ] Export search results to HAR/JSON/CSV
- [ ] Real-time file watching with inotify
- [ ] Web UI for browsing
- [ ] Request/Response diff tool
- [ ] GraphQL API support
- [ ] WebSocket message parsing
- [ ] HTTP/2 and HTTP/3 support detection
- [ ] Request replay functionality
- [ ] Custom filter templates

---

## Notes

- All text in code, documentation, and commit messages must be in English
- Follow conventional commit format: `feat:`, `fix:`, `docs:`, etc.
- Use snake_case for Python code
- Use kebab-case for CLI commands
- Target Python 3.10+ (as per project requirements)

---

## Success Criteria

✅ **Functional:**
- All MCP tools work correctly
- All CLI commands work correctly
- Database schema is normalized and performant
- File tracking with change detection works

✅ **Performance:**
- All operations meet performance targets
- Memory usage is reasonable (<500MB for typical workloads)
- Database size is optimized

✅ **Quality:**
- >90% test coverage
- Clear documentation
- Error messages are helpful
- Code follows project conventions

✅ **User Experience:**
- Easy installation
- Intuitive CLI commands
- Works seamlessly with Claude Desktop
- Clear feedback for all operations

---

**End of Plan**
