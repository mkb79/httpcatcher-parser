# httpcatcher-parser

**httpcatcher-parser** is a Python library and CLI suite to parse binary **[HTTP Catcher](https://httpcatcher.com)** session files. It provides multiple tools:

- **Session parser and inspector** (`hc-parser`)
- **HAR format exporter** (`hc-har`)
- **MCP server with full-text search** (`hc-mcp`) - Claude Desktop integration

> ⚠️ Not affiliated with or endorsed by "HTTP Catcher". Use responsibly.

---

## Features

### Core Tools
- **Binary session parsing** - Parse HTTP Catcher session files with detailed frame analysis
- **HAR export** - Convert sessions to standard HAR format for use with other tools
- **Coverage reporting** - Identify unparsed byte ranges in session files

### MCP Server (Claude Desktop Integration)
- **Full-text search** - SQLite FTS5 search across URLs, headers, cookies, and bodies
- **Flexible filtering** - Filter by method, status code, host, content type, date ranges
- **Context optimization** - Granular field selection and body format control
- **Async/parallel processing** - Fast indexing with concurrent file processing
- **Body decompression** - Automatic gzip/brotli/deflate decompression during indexing
- **Pagination support** - Efficient browsing of large result sets

> ⚠️ Not affiliated with or endorsed by "HTTP Catcher". Use responsibly.

---

## CLI Tools

### `hc-parser` -- Session Inspector

Parses a session file and optionally reports unparsed offset ranges ("gaps").

```bash
Usage: hc-parser SESSION [--outdir DIR] [--report-gaps] [--min-gap-bytes N]

Positional arguments:
  SESSION                 Path to session file to parse

Options:
  --outdir DIR            Output directory. Default: result_{basename_without_suffix}
  --report-gaps           Report unparsed offset ranges and coverage
  --min-gap-bytes N       Only include gaps >= N bytes in the .gaps.txt report (default 1)
```

**Example:**
```bash
uv run hc-parser session.hcs --report-gaps --min-gap-bytes 32
```

### `hc-har` -- HAR Exporter

Converts a session file to HAR format.

```bash
Usage: hc-har SESSION [--outdir DIR] [--outfile NAME] [--include-payload] [--no-decompress]

Positional arguments:
  SESSION                 Path to session file

Options:
  --outdir DIR            Output directory (default: result_{basename})
  --outfile NAME          Output HAR file name (default: <basename>.har)
  --include-payload       Include request/response bodies in HAR
  --no-decompress         Do not decompress response bodies
```

**Example:**
```bash
uv run hc-har session.hcs --outfile output.har --include-payload
```

### `hc-mcp` -- MCP Server and Database Manager

Manage HTTP Catcher sessions in a SQLite database with MCP server for Claude Desktop.

```bash
Usage: hc-mcp [--data-dir DIR] COMMAND [OPTIONS]

Commands:
  init                    Initialize directory structure and database
  server                  Start MCP server (stdio mode for Claude Desktop)
  db                      Database management (check, reset, backup, restore)
  files                   Session file management (list, add, remove, reindex)
  search                  Search HTTP requests with filters
  stats                   Show database statistics
  keys                    List available header/cookie keys
  details                 Show request details with optional body display
  config                  Configuration management

Global Options:
  --data-dir DIR          Data directory (default: ~/.http_catcher)
```

#### Common Commands

**Initialize database:**
```bash
uv run --extra mcp hc-mcp init
```

**Index session files:**
```bash
# Add single file
uv run --extra mcp hc-mcp files add session.hcs

# Add all files from directory
uv run --extra mcp hc-mcp files add /path/to/sessions/*.hcs

# Reindex all files
uv run --extra mcp hc-mcp files reindex --all
```

**Search requests:**
```bash
# Search by URL
uv run --extra mcp hc-mcp search --url "api.example.com"

# Search by method and status
uv run --extra mcp hc-mcp search --method POST --status 200

# Full-text search in bodies
uv run --extra mcp hc-mcp search --body-search "error"

# Filter by date range
uv run --extra mcp hc-mcp search --after "2025-01-01" --before "2025-12-31"

# Limit results
uv run --extra mcp hc-mcp search --limit 10 --offset 20

# Filter by specific session files (by ID, name, or path)
uv run --extra mcp hc-mcp search --file session1.hcs --method POST
uv run --extra mcp hc-mcp search --file 1 --file 3  # Multiple files
```

**View request details:**
```bash
# Summary view (minimal context)
uv run --extra mcp hc-mcp details 12345

# Full details with complete body
uv run --extra mcp hc-mcp details 12345 --level full --full-body

# Pretty-print JSON bodies
uv run --extra mcp hc-mcp details 12345 --level full --pretty-json
```

**Database statistics:**
```bash
uv run --extra mcp hc-mcp stats
```

**Start MCP server for Claude Desktop:**
```bash
uv run --extra mcp hc-mcp server
```

---

## Installation

### Quick Start with `uv`

**Run once from Git (no install):**
```bash
uvx --from git+https://github.com/mkb79/httpcatcher-parser.git hc-parser --help
uvx --from git+https://github.com/mkb79/httpcatcher-parser.git hc-har --help
```

**Install as global tool (from Git):**
```bash
# Core tools only
uv tool install git+https://github.com/mkb79/httpcatcher-parser.git

# With MCP server
uv tool install "git+https://github.com/mkb79/httpcatcher-parser.git[mcp]"

# Usage
hc-parser --help
hc-har --help
hc-mcp --help
```

**Local development:**
```bash
# Run without installing
uv run hc-parser session.hcs --report-gaps
uv run hc-har session.hcs --include-payload
uv run --extra mcp hc-mcp init

# Install as tool from current path
uv tool install --path . --extra mcp
```

---

## Claude Desktop Integration

Add to your Claude Desktop MCP settings (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "httpcatcher": {
      "command": "uv",
      "args": [
        "tool",
        "run",
        "--from",
        "git+https://github.com/mkb79/httpcatcher-parser.git[mcp]",
        "hc-mcp",
        "server"
      ]
    }
  }
}
```

Or if installed locally:
```json
{
  "mcpServers": {
    "httpcatcher": {
      "command": "uv",
      "args": ["run", "--extra", "mcp", "hc-mcp", "server"],
      "cwd": "/path/to/httpcatcher-parser"
    }
  }
}
```

### Available MCP Tools

Once configured, Claude Desktop can use these tools:

- **`search_requests`** - Search with flexible filters and field selection
- **`get_request_details`** - Get detailed request/response information
- **`get_stats`** - Get database statistics and breakdowns
- **`list_files`** - List indexed session files with request counts
- **`get_available_keys`** - Get all header/cookie key names
- **`autocomplete_key`** - Autocomplete header/cookie keys
- **`index_file`** - Index a new session file into database

**Typical Workflow with MCP:**
```
1. List available session files:
   → list_files(sort_by="date")

2. Search in specific files:
   → search_requests(file_ids=[1, 3], method="POST")

3. Get request details:
   → get_request_details(request_id=12345, detail_level="full")
```

**Context Optimization:**
- Use `fields: "minimal"` for 85% token reduction
- Use `detail_level: "summary"` for 95% token reduction
- Set `body_format: "size_only"` to skip body content
- Enable `sparse_mode` for absolute minimum data
- Filter by `file_ids` to search only relevant sessions

---

## Exporting Sessions from HTTP Catcher

Session files must first be exported from the [HTTP Catcher app](https://httpcatcher.com).

1. Open HTTP Catcher and go to **Saved Sessions**
2. Swipe **left** on the desired session
3. Tap **Share**
4. Choose **Save to Files**
5. Select a location (e.g., iCloud Drive, On My iPhone)

Transfer the saved `.hcs` file to your computer and use with any CLI tool.

---

## Project Layout

```
.
├── src/
│   └── httpcatcher_parser/
│       ├── hc_parser.py           # Binary session parser
│       ├── hc_har.py              # HAR exporter
│       └── mcp/                   # MCP server implementation
│           ├── server.py          # MCP server with tools
│           ├── cli.py             # CLI argument parser
│           ├── manage.py          # CLI command handlers
│           ├── database.py        # Async SQLite wrapper
│           ├── indexer.py         # Session file indexer
│           ├── engine.py          # Search and query engine
│           ├── detail_fetcher.py  # Request detail loader
│           ├── file_tracker.py    # File hash tracking
│           └── content_analyzer.py # Content-Type categorization
├── tests/
│   └── test_parallel_import.py    # Async processing tests
├── pyproject.toml
├── README.md
├── Specification.md
└── .python-version
```

---

## Development

**Code quality:**
```bash
# Check and auto-fix linting issues
uv run ruff check --fix .

# Format code
uv run ruff format .
```

**Run tests:**
```bash
uv run python tests/test_parallel_import.py
```

---

## License

AGPL-3.0-only © 2025 [mkb79](mailto:mkb79@hackitall.de)
