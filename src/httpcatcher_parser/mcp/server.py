"""MCP server implementation for HTTP Catcher session analysis."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from mcp.server import Server
from mcp.types import Resource, Tool

from .database import Database
from .detail_fetcher import DetailFetcher, DetailLevel
from .file_tracker import FileTracker
from .indexer import Indexer
from .query_engine import (
    CookieFilter,
    HeaderFilter,
    MatchMode,
    QueryEngine,
    SearchFilters,
    TimeRange,
)
from ..hc_parser import HttpCatcherScanner

# Configure logging
logger = logging.getLogger(__name__)


class HttpCatcherMCPServer:
    """MCP server for HTTP Catcher session analysis."""

    def __init__(self, data_dir: Path):
        """Initialize MCP server.

        Args:
            data_dir: Data directory containing database and config
        """
        self.data_dir = data_dir
        self.db_path = data_dir / "index.db"
        self.db: Optional[Database] = None
        self.server = Server("httpcatcher-mcp")

        # Initialize components
        self._init_database()
        self._register_handlers()

    def _init_database(self):
        """Initialize database connection."""
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {self.db_path}. Run 'hc-mcp init' first."
            )

        self.db = Database(self.db_path)
        logger.info(f"Connected to database: {self.db_path}")

    def _register_handlers(self):
        """Register MCP handlers."""
        # List available tools
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available tools."""
            return [
                Tool(
                    name="search_requests",
                    description=(
                        "Search HTTP requests with flexible filters. "
                        "Supports filtering by method, URL, host, path, status codes, "
                        "headers, cookies, body content, time ranges, and content types."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "method": {"type": "string", "description": "HTTP method (GET, POST, etc.)"},
                            "url": {"type": "string", "description": "URL pattern (partial match)"},
                            "host": {"type": "string", "description": "Host pattern (partial match)"},
                            "path": {"type": "string", "description": "Path pattern (partial match)"},
                            "status_codes": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Specific status codes to match"
                            },
                            "status_min": {"type": "integer", "description": "Minimum status code"},
                            "status_max": {"type": "integer", "description": "Maximum status code"},
                            "req_content_type": {"type": "string", "description": "Request content type"},
                            "resp_content_type": {"type": "string", "description": "Response content type"},
                            "resp_content_category": {
                                "type": "string",
                                "enum": ["json", "image", "media", "websocket", "html", "css", "javascript", "font", "xml", "pdf", "binary", "other"],
                                "description": "Response content category"
                            },
                            "time_start": {"type": "integer", "description": "Start timestamp (Unix)"},
                            "time_end": {"type": "integer", "description": "End timestamp (Unix)"},
                            "headers": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "key": {"type": "string"},
                                        "value": {"type": "string"},
                                        "request": {"type": "boolean", "default": True}
                                    }
                                },
                                "description": "Header filters (key and/or value)"
                            },
                            "cookies": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "key": {"type": "string"},
                                        "value": {"type": "string"},
                                        "request": {"type": "boolean", "default": True}
                                    }
                                },
                                "description": "Cookie filters (key and/or value)"
                            },
                            "body_search": {"type": "string", "description": "Search in request/response bodies"},
                            "body_in_request": {"type": "boolean", "default": True, "description": "Search in request bodies"},
                            "body_in_response": {"type": "boolean", "default": True, "description": "Search in response bodies"},
                            "file_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Limit search to specific file IDs"
                            },
                            "limit": {"type": "integer", "default": 100, "description": "Max results"},
                            "offset": {"type": "integer", "default": 0, "description": "Skip N results"},
                            "sort_by": {
                                "type": "string",
                                "enum": ["req_timestamp", "resp_timestamp", "duration_ms", "status_code"],
                                "default": "req_timestamp"
                            },
                            "sort_desc": {"type": "boolean", "default": True}
                        }
                    }
                ),
                Tool(
                    name="get_request_details",
                    description=(
                        "Get detailed information about a specific request by ID. "
                        "Supports selective field loading and optional body decompression."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "request_id": {"type": "integer", "description": "Request ID"},
                            "detail_level": {
                                "type": "string",
                                "enum": ["full", "headers_only", "request_only", "response_only", "metadata"],
                                "default": "full",
                                "description": "Level of detail to fetch"
                            },
                            "decompress": {
                                "type": "boolean",
                                "default": False,
                                "description": "Decompress compressed bodies"
                            }
                        },
                        "required": ["request_id"]
                    }
                ),
                Tool(
                    name="get_stats",
                    description=(
                        "Get statistics about HTTP requests. "
                        "Includes distribution by method, status, content type, and top hosts."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_id": {
                                "type": "integer",
                                "description": "Optional file ID to filter stats"
                            }
                        }
                    }
                ),
                Tool(
                    name="list_files",
                    description="List all indexed session files with statistics.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "sort_by": {
                                "type": "string",
                                "enum": ["name", "date", "size", "requests"],
                                "default": "date"
                            }
                        }
                    }
                ),
                Tool(
                    name="get_available_keys",
                    description=(
                        "Get all available header or cookie key names with usage counts. "
                        "Useful for discovering what headers/cookies are present."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "key_type": {
                                "type": "string",
                                "enum": ["header", "cookie"],
                                "default": "header"
                            }
                        },
                        "required": ["key_type"]
                    }
                ),
                Tool(
                    name="autocomplete_key",
                    description="Autocomplete header or cookie key names by prefix.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "prefix": {"type": "string", "description": "Key prefix"},
                            "key_type": {
                                "type": "string",
                                "enum": ["header", "cookie"],
                                "default": "header"
                            },
                            "limit": {"type": "integer", "default": 20}
                        },
                        "required": ["prefix", "key_type"]
                    }
                ),
                Tool(
                    name="index_file",
                    description="Index a new session file into the database.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "Path to session file"},
                            "force_reindex": {
                                "type": "boolean",
                                "default": False,
                                "description": "Force reindex even if unchanged"
                            }
                        },
                        "required": ["file_path"]
                    }
                ),
            ]

        # Call tool handler
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> list[Any]:
            """Handle tool calls."""
            try:
                if name == "search_requests":
                    return await self._search_requests(arguments)
                elif name == "get_request_details":
                    return await self._get_request_details(arguments)
                elif name == "get_stats":
                    return await self._get_stats(arguments)
                elif name == "list_files":
                    return await self._list_files(arguments)
                elif name == "get_available_keys":
                    return await self._get_available_keys(arguments)
                elif name == "autocomplete_key":
                    return await self._autocomplete_key(arguments)
                elif name == "index_file":
                    return await self._index_file(arguments)
                else:
                    raise ValueError(f"Unknown tool: {name}")
            except Exception as e:
                logger.error(f"Error calling tool {name}: {e}", exc_info=True)
                return [{"error": str(e)}]

    async def _search_requests(self, args: dict) -> list[dict]:
        """Handle search_requests tool."""
        filters = SearchFilters(
            method=args.get("method"),
            url=args.get("url"),
            host=args.get("host"),
            path=args.get("path"),
            status_codes=args.get("status_codes"),
            status_min=args.get("status_min"),
            status_max=args.get("status_max"),
            req_content_type=args.get("req_content_type"),
            resp_content_type=args.get("resp_content_type"),
            resp_content_category=args.get("resp_content_category"),
            body_search=args.get("body_search"),
            body_in_request=args.get("body_in_request", True),
            body_in_response=args.get("body_in_response", True),
            file_ids=args.get("file_ids"),
            limit=args.get("limit", 100),
            offset=args.get("offset", 0),
            sort_by=args.get("sort_by", "req_timestamp"),
            sort_desc=args.get("sort_desc", True)
        )

        # Time range
        if args.get("time_start") or args.get("time_end"):
            filters.time_range = TimeRange(
                start=args.get("time_start"),
                end=args.get("time_end")
            )

        # Header filters
        if args.get("headers"):
            for h in args["headers"]:
                filters.headers.append(HeaderFilter(
                    key=h.get("key"),
                    value=h.get("value"),
                    request=h.get("request", True)
                ))

        # Cookie filters
        if args.get("cookies"):
            for c in args["cookies"]:
                filters.cookies.append(CookieFilter(
                    key=c.get("key"),
                    value=c.get("value"),
                    request=c.get("request", True)
                ))

        engine = QueryEngine(self.db)
        results = engine.search_requests(filters)
        total = engine.count_requests(filters)

        return [{
            "total": total,
            "count": len(results),
            "offset": filters.offset,
            "limit": filters.limit,
            "results": results
        }]

    async def _get_request_details(self, args: dict) -> list[dict]:
        """Handle get_request_details tool."""
        request_id = args["request_id"]

        # Map string to enum
        level_map = {
            "full": DetailLevel.FULL,
            "headers_only": DetailLevel.HEADERS_ONLY,
            "request_only": DetailLevel.REQUEST_ONLY,
            "response_only": DetailLevel.RESPONSE_ONLY,
            "metadata": DetailLevel.METADATA
        }
        detail_level = level_map.get(args.get("detail_level", "full"), DetailLevel.FULL)

        fetcher = DetailFetcher(self.db)
        details = fetcher.get_request_details(
            request_id,
            detail_level,
            args.get("decompress", False)
        )

        if not details:
            return [{"error": f"Request not found: {request_id}"}]

        # Convert bytes to base64 for JSON serialization
        def make_json_safe(obj):
            if isinstance(obj, bytes):
                import base64
                return {
                    "_type": "base64",
                    "data": base64.b64encode(obj).decode('ascii')
                }
            elif isinstance(obj, dict):
                return {k: make_json_safe(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [make_json_safe(item) for item in obj]
            return obj

        return [make_json_safe(details)]

    async def _get_stats(self, args: dict) -> list[dict]:
        """Handle get_stats tool."""
        file_id = args.get("file_id")

        engine = QueryEngine(self.db)
        stats = engine.get_stats(file_id)

        return [stats]

    async def _list_files(self, args: dict) -> list[dict]:
        """Handle list_files tool."""
        tracker = FileTracker(self.db)
        files = tracker.list_files()

        # Sort
        sort_by = args.get("sort_by", "date")
        if sort_by == "name":
            files.sort(key=lambda f: f['filename'])
        elif sort_by == "size":
            files.sort(key=lambda f: f['file_size'], reverse=True)
        elif sort_by == "requests":
            files.sort(key=lambda f: f['request_count'], reverse=True)
        else:  # date
            files.sort(key=lambda f: f['indexed_at'], reverse=True)

        return files

    async def _get_available_keys(self, args: dict) -> list[dict]:
        """Handle get_available_keys tool."""
        key_type = args["key_type"]

        engine = QueryEngine(self.db)
        keys = engine.get_available_keys(key_type)

        return keys

    async def _autocomplete_key(self, args: dict) -> list[str]:
        """Handle autocomplete_key tool."""
        prefix = args["prefix"]
        key_type = args["key_type"]
        limit = args.get("limit", 20)

        engine = QueryEngine(self.db)
        keys = engine.autocomplete_key(prefix, key_type, limit)

        return keys

    async def _index_file(self, args: dict) -> list[dict]:
        """Handle index_file tool."""
        file_path = Path(args["file_path"])
        force_reindex = args.get("force_reindex", False)

        scanner = HttpCatcherScanner.default()
        indexer = Indexer(self.db, scanner)

        try:
            result = indexer.index_file(file_path, force_reindex)
            return [result]
        except Exception as e:
            return [{"error": str(e)}]

    def run(self):
        """Run the MCP server (stdio mode)."""
        import asyncio
        from mcp.server.stdio import stdio_server

        logger.info("Starting HTTP Catcher MCP server in stdio mode")

        async def _run():
            async with stdio_server() as (read_stream, write_stream):
                await self.server.run(
                    read_stream,
                    write_stream,
                    self.server.create_initialization_options()
                )

        asyncio.run(_run())


def main(data_dir: Path, log_level: str = "info"):
    """Main entry point for MCP server.

    Args:
        data_dir: Data directory
        log_level: Logging level
    """
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Create and run server
    server = HttpCatcherMCPServer(data_dir)
    server.run()
