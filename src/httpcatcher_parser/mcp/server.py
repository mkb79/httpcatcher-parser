"""MCP server implementation for HTTP Catcher session analysis."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent

from .database import Database
from .detail_fetcher import DetailFetcher, DetailLevel
from .file_tracker import FileTracker
from .indexer import Indexer
from .query_engine import (
    CookieFilter,
    HeaderFilter,
    QueryEngine,
    SearchFilters,
    TimeRange,
)
from ..hc_parser import HttpCatcherScanner

# Configure logging
logger = logging.getLogger(__name__)


class HttpCatcherMCPServer:
    """MCP server for HTTP Catcher session analysis (async version)."""

    def __init__(self, data_dir: Path):
        """Initialize MCP server.

        Args:
            data_dir: Data directory containing database and config
        """
        self.data_dir = data_dir
        self.db_path = data_dir / "index.db"
        self.server = Server("httpcatcher-mcp")

        # Check database exists
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {self.db_path}. Run 'hc-mcp init' first."
            )

        logger.info(f"MCP server initialized with database: {self.db_path}")
        self._register_handlers()

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
                        "Search HTTP requests with flexible filters and optimized field selection.\n\n"
                        "CONTEXT OPTIMIZATION:\n"
                        "- Use 'fields' to select only needed fields (default: minimal set)\n"
                        "- Set 'limit' to 10 or less for quick overviews\n"
                        "- Use 'sparse_mode=true' for absolute minimum data\n\n"
                        "PAGINATION:\n"
                        "- Use 'limit' and 'offset' to page through results\n"
                        "- Response includes 'has_more', 'next_offset', 'page', 'total_pages'\n"
                        "- Example: offset=0 (page 1), offset=10 (page 2), offset=20 (page 3)\n"
                        "- Check 'has_more' to see if more results exist\n\n"
                        "FIELD PRESETS:\n"
                        "- minimal: id, method, url, status_code (85% reduction)\n"
                        "- standard: + host, path, content types\n"
                        "- extended: + timestamps, duration\n"
                        "- full: all fields (high context cost!)\n\n"
                        "FILTERS:\n"
                        "Supports filtering by method, URL, host, path, status codes, "
                        "headers, cookies, body content, time ranges, and content types.\n\n"
                        "FILE FILTERING:\n"
                        "- Use 'file_ids' to search only in specific session files\n"
                        "- Get file IDs with the 'list_files' tool\n"
                        "- Example: file_ids=[1, 3] searches only in files 1 and 3"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "method": {
                                "type": "string",
                                "description": "HTTP method (GET, POST, etc.)",
                            },
                            "url": {
                                "type": "string",
                                "description": "URL pattern (partial match)",
                            },
                            "host": {
                                "type": "string",
                                "description": "Host pattern (partial match)",
                            },
                            "path": {
                                "type": "string",
                                "description": "Path pattern (partial match)",
                            },
                            "status_codes": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Specific status codes to match",
                            },
                            "status_min": {
                                "type": "integer",
                                "description": "Minimum status code",
                            },
                            "status_max": {
                                "type": "integer",
                                "description": "Maximum status code",
                            },
                            "req_content_type": {
                                "type": "string",
                                "description": "Request content type",
                            },
                            "resp_content_type": {
                                "type": "string",
                                "description": "Response content type",
                            },
                            "resp_content_category": {
                                "type": "string",
                                "enum": [
                                    "json",
                                    "image",
                                    "media",
                                    "websocket",
                                    "html",
                                    "css",
                                    "javascript",
                                    "font",
                                    "xml",
                                    "pdf",
                                    "binary",
                                    "other",
                                ],
                                "description": "Response content category",
                            },
                            "time_start": {
                                "type": "integer",
                                "description": "Start timestamp (Unix ms)",
                            },
                            "time_end": {
                                "type": "integer",
                                "description": "End timestamp (Unix ms)",
                            },
                            "headers": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "key": {"type": "string"},
                                        "value": {"type": "string"},
                                        "request": {"type": "boolean", "default": True},
                                    },
                                },
                                "description": "Header filters (key and/or value)",
                            },
                            "cookies": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "key": {"type": "string"},
                                        "value": {"type": "string"},
                                        "request": {"type": "boolean", "default": True},
                                    },
                                },
                                "description": "Cookie filters (key and/or value)",
                            },
                            "body_search": {
                                "type": "string",
                                "description": "Search in request/response bodies",
                            },
                            "body_in_request": {
                                "type": "boolean",
                                "default": True,
                                "description": "Search in request bodies",
                            },
                            "body_in_response": {
                                "type": "boolean",
                                "default": True,
                                "description": "Search in response bodies",
                            },
                            "file_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Limit search to specific file IDs",
                            },
                            "limit": {
                                "type": "integer",
                                "default": 10,
                                "description": "Max results (default: 10 for context efficiency)",
                            },
                            "offset": {
                                "type": "integer",
                                "default": 0,
                                "description": "Skip N results",
                            },
                            "sort_by": {
                                "type": "string",
                                "enum": [
                                    "req_timestamp",
                                    "resp_timestamp",
                                    "duration_ms",
                                    "status_code",
                                ],
                                "default": "req_timestamp",
                            },
                            "sort_desc": {"type": "boolean", "default": True},
                            "fields": {
                                "type": "string",
                                "enum": [
                                    "minimal",
                                    "standard",
                                    "extended",
                                    "full",
                                    "custom",
                                ],
                                "default": "minimal",
                                "description": "Field preset (minimal=id/method/url/status, standard=+host/path/types, extended=+timestamps/duration, full=all)",
                            },
                            "custom_fields": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": [
                                        "id",
                                        "method",
                                        "url",
                                        "host",
                                        "path",
                                        "status_code",
                                        "req_timestamp",
                                        "resp_timestamp",
                                        "duration_ms",
                                        "req_content_type",
                                        "resp_content_type",
                                        "resp_content_category",
                                        "connection_id",
                                        "file_id",
                                    ],
                                },
                                "description": "Custom field selection (only with fields='custom')",
                            },
                            "sparse_mode": {
                                "type": "boolean",
                                "default": False,
                                "description": "Ultra-minimal mode: only id and url (max context saving)",
                            },
                        },
                    },
                ),
                Tool(
                    name="get_request_details",
                    description=(
                        "Get detailed information about a specific request by ID with granular control.\n\n"
                        "CONTEXT OPTIMIZATION:\n"
                        "- Use detail_level='summary' for metadata only (95% reduction)\n"
                        "- Set max_body_size to limit body tokens (e.g., 1000 bytes)\n"
                        "- Use body_format='size_only' to see sizes without content\n"
                        "- Disable headers/cookies if not needed\n\n"
                        "DETAIL LEVELS:\n"
                        "- summary: Only metadata (method, url, status, sizes) - minimal tokens\n"
                        "- metadata: Basic info without headers/bodies\n"
                        "- headers_only: Metadata + headers/cookies, no bodies\n"
                        "- request_only: Request side only\n"
                        "- response_only: Response side only\n"
                        "- full: Everything (warning: can be 100k+ tokens!)\n\n"
                        "BODY FORMATS:\n"
                        "- none: No body content\n"
                        "- preview: First 500 bytes (pre-stored)\n"
                        "- size_only: Just body size info\n"
                        "- truncated: Up to max_body_size bytes\n"
                        "- full: Complete body (use with caution!)"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "request_id": {
                                "type": "integer",
                                "description": "Request ID",
                            },
                            "detail_level": {
                                "type": "string",
                                "enum": [
                                    "summary",
                                    "metadata",
                                    "headers_only",
                                    "request_only",
                                    "response_only",
                                    "full",
                                ],
                                "default": "summary",
                                "description": "Level of detail (summary is most context-efficient)",
                            },
                            "max_body_size": {
                                "type": "integer",
                                "default": 2000,
                                "description": "Max body bytes to return (0=preview only, null=unlimited)",
                            },
                            "body_format": {
                                "type": "string",
                                "enum": [
                                    "none",
                                    "preview",
                                    "size_only",
                                    "truncated",
                                    "full",
                                ],
                                "default": "truncated",
                                "description": "How to return body content",
                            },
                            "include_headers": {
                                "type": "boolean",
                                "default": True,
                                "description": "Include request/response headers",
                            },
                            "include_cookies": {
                                "type": "boolean",
                                "default": True,
                                "description": "Include cookies",
                            },
                        },
                        "required": ["request_id"],
                    },
                ),
                Tool(
                    name="get_stats",
                    description=(
                        "Get statistics about HTTP requests with configurable detail level.\n\n"
                        "CONTEXT OPTIMIZATION:\n"
                        "- Use compact=true for just totals (90% reduction)\n"
                        "- Set top_n=5 to limit host lists\n"
                        "- Disable breakdowns if not needed\n\n"
                        "MODES:\n"
                        "- compact: Only total counts, no breakdowns\n"
                        "- standard: Top 10 hosts and basic breakdowns\n"
                        "- detailed: All breakdowns and top 50 hosts (high context cost)"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_id": {
                                "type": "integer",
                                "description": "Optional file ID to filter stats",
                            },
                            "compact": {
                                "type": "boolean",
                                "default": False,
                                "description": "Return only totals, skip breakdowns (90% context reduction)",
                            },
                            "top_n": {
                                "type": "integer",
                                "default": 10,
                                "description": "Number of top hosts to return (was 50)",
                            },
                            "include_breakdowns": {
                                "type": "boolean",
                                "default": True,
                                "description": "Include method/status/content breakdowns",
                            },
                            "include_time_range": {
                                "type": "boolean",
                                "default": True,
                                "description": "Include time range statistics",
                            },
                        },
                    },
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
                                "default": "date",
                            }
                        },
                    },
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
                                "default": "header",
                            }
                        },
                        "required": ["key_type"],
                    },
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
                                "default": "header",
                            },
                            "limit": {"type": "integer", "default": 20},
                        },
                        "required": ["prefix", "key_type"],
                    },
                ),
                Tool(
                    name="index_file",
                    description="Index a new session file into the database.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Path to session file",
                            },
                            "force_reindex": {
                                "type": "boolean",
                                "default": False,
                                "description": "Force reindex even if unchanged",
                            },
                        },
                        "required": ["file_path"],
                    },
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
                import json

                return [
                    TextContent(
                        type="text", text=json.dumps({"error": str(e)}, indent=2)
                    )
                ]

    async def _search_requests(self, args: dict) -> list[TextContent]:
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
            sort_desc=args.get("sort_desc", True),
        )

        # Time range
        if args.get("time_start") or args.get("time_end"):
            filters.time_range = TimeRange(
                start=args.get("time_start"), end=args.get("time_end")
            )

        # Header filters
        if args.get("headers"):
            for h in args["headers"]:
                filters.headers.append(
                    HeaderFilter(
                        key=h.get("key"),
                        value=h.get("value"),
                        request=h.get("request", True),
                    )
                )

        # Cookie filters
        if args.get("cookies"):
            for c in args["cookies"]:
                filters.cookies.append(
                    CookieFilter(
                        key=c.get("key"),
                        value=c.get("value"),
                        request=c.get("request", True),
                    )
                )

        # Use async database connection
        async with Database(self.db_path) as db:
            engine = QueryEngine(db)
            results = await engine.search_requests(filters)
            total = await engine.count_requests(filters)

        # Apply field selection/projection
        field_preset = args.get("fields", "minimal")
        sparse_mode = args.get("sparse_mode", False)

        if sparse_mode:
            # Ultra-minimal: only id and url
            results = [{"id": r["id"], "url": r["url"]} for r in results]
        elif field_preset == "minimal":
            # Essential fields only (85% reduction)
            results = [
                {k: r[k] for k in ["id", "method", "url", "status_code"] if k in r}
                for r in results
            ]
        elif field_preset == "standard":
            # Add host, path, content types
            fields = [
                "id",
                "method",
                "url",
                "host",
                "path",
                "status_code",
                "req_content_type",
                "resp_content_type",
                "resp_content_category",
            ]
            results = [{k: r[k] for k in fields if k in r} for r in results]
        elif field_preset == "extended":
            # Add timestamps and duration
            fields = [
                "id",
                "method",
                "url",
                "host",
                "path",
                "status_code",
                "req_content_type",
                "resp_content_type",
                "resp_content_category",
                "req_timestamp",
                "resp_timestamp",
                "duration_ms",
            ]
            results = [{k: r[k] for k in fields if k in r} for r in results]
        elif field_preset == "custom":
            # Custom field selection
            custom_fields = args.get("custom_fields", ["id", "url", "status_code"])
            results = [{k: r[k] for k in custom_fields if k in r} for r in results]
        # else: full - keep all fields

        import json

        # Add pagination hints for easier navigation
        has_more = (filters.offset + len(results)) < total
        next_offset = filters.offset + len(results) if has_more else None
        current_page = (filters.offset // filters.limit) + 1 if filters.limit > 0 else 1
        total_pages = (
            (total + filters.limit - 1) // filters.limit if filters.limit > 0 else 1
        )

        result_data = {
            "total": total,
            "count": len(results),
            "offset": filters.offset,
            "limit": filters.limit,
            "field_preset": "sparse" if sparse_mode else field_preset,
            # Pagination hints
            "has_more": has_more,
            "next_offset": next_offset,
            "page": current_page,
            "total_pages": total_pages,
            "results": results,
        }
        return [TextContent(type="text", text=json.dumps(result_data, indent=2))]

    async def _get_request_details(self, args: dict) -> list[TextContent]:
        """Handle get_request_details tool with context optimization."""
        request_id = args["request_id"]

        # Map string to enum
        level_map = {
            "summary": DetailLevel.METADATA,  # summary = metadata + sizes
            "metadata": DetailLevel.METADATA,
            "headers_only": DetailLevel.HEADERS_ONLY,
            "request_only": DetailLevel.REQUEST_ONLY,
            "response_only": DetailLevel.RESPONSE_ONLY,
            "full": DetailLevel.FULL,
        }
        detail_level = level_map.get(
            args.get("detail_level", "summary"), DetailLevel.METADATA
        )

        # Use async database connection
        async with Database(self.db_path) as db:
            fetcher = DetailFetcher(db)
            details = await fetcher.get_request_details(request_id, detail_level)

        if not details:
            import json

            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"error": f"Request not found: {request_id}"}, indent=2
                    ),
                )
            ]

        # Apply context optimizations
        body_format = args.get("body_format", "truncated")
        max_body_size = args.get("max_body_size", 2000)
        include_headers = args.get("include_headers", True)
        include_cookies = args.get("include_cookies", True)

        # Post-process headers/cookies removal
        if not include_headers:
            if "request" in details:
                details["request"].pop("headers", None)
            if "response" in details:
                details["response"].pop("headers", None)

        if not include_cookies:
            if "request" in details:
                details["request"].pop("cookies", None)
            if "response" in details:
                details["response"].pop("cookies", None)

        # Post-process body truncation/format
        for side in ["request", "response"]:
            if side in details:
                self._process_body(details[side], body_format, max_body_size)

        # Convert bytes to base64 for JSON serialization
        def make_json_safe(obj):
            if isinstance(obj, bytes):
                import base64

                # Truncate if needed
                if max_body_size and len(obj) > max_body_size:
                    obj = obj[:max_body_size]
                return {
                    "_type": "base64",
                    "data": base64.b64encode(obj).decode("ascii"),
                    "_truncated": True
                    if max_body_size and len(obj) >= max_body_size
                    else False,
                }
            elif isinstance(obj, dict):
                return {k: make_json_safe(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [make_json_safe(item) for item in obj]
            return obj

        import json

        return [
            TextContent(type="text", text=json.dumps(make_json_safe(details), indent=2))
        ]

    def _process_body(self, side_details: dict, body_format: str, max_body_size: int):
        """Process body according to format and size constraints."""
        if body_format == "none":
            # Remove all body content
            side_details.pop("body", None)
            side_details.pop("body_preview", None)
        elif body_format == "size_only":
            # Keep only size info
            side_details.pop("body", None)
            side_details.pop("body_preview", None)
            # body_size already present
        elif body_format == "preview":
            # Keep only preview, remove full body
            side_details.pop("body", None)
            # body_preview already present
        elif body_format == "truncated" and max_body_size:
            # Truncate body to max_body_size
            if "body" in side_details:
                body = side_details["body"]
                if isinstance(body, bytes) and len(body) > max_body_size:
                    side_details["body"] = body[:max_body_size]
                    side_details["body_truncated"] = True
                    side_details["body_full_size"] = len(body)
        # elif body_format == "full": keep as is

    async def _get_stats(self, args: dict) -> list[TextContent]:
        """Handle get_stats tool with context optimization."""
        file_id = args.get("file_id")
        compact = args.get("compact", False)
        top_n = args.get("top_n", 10)
        include_breakdowns = args.get("include_breakdowns", True)
        include_time_range = args.get("include_time_range", True)

        # Use async database connection
        async with Database(self.db_path) as db:
            engine = QueryEngine(db)
            stats = await engine.get_stats(file_id)

        # Apply context optimizations
        if compact:
            # Ultra-compact: only totals (90% reduction)
            stats = {"total_requests": stats["total_requests"], "mode": "compact"}
        else:
            # Limit top hosts
            if "top_hosts" in stats:
                stats["top_hosts"] = dict(list(stats["top_hosts"].items())[:top_n])

            # Remove breakdowns if not wanted
            if not include_breakdowns:
                stats.pop("by_method", None)
                stats.pop("by_status", None)
                stats.pop("by_content_category", None)

            if not include_time_range:
                stats.pop("time_range", None)

        import json

        return [TextContent(type="text", text=json.dumps(stats, indent=2))]

    async def _list_files(self, args: dict) -> list[TextContent]:
        """Handle list_files tool."""
        # Use async database connection
        async with Database(self.db_path) as db:
            tracker = FileTracker(db)
            files = await tracker.list_files()

        # Sort
        sort_by = args.get("sort_by", "date")
        if sort_by == "name":
            files.sort(key=lambda f: f["filename"])
        elif sort_by == "size":
            files.sort(key=lambda f: f["file_size"], reverse=True)
        elif sort_by == "requests":
            files.sort(key=lambda f: f["request_count"], reverse=True)
        else:  # date
            files.sort(key=lambda f: f["indexed_at"], reverse=True)

        import json

        return [TextContent(type="text", text=json.dumps(files, indent=2))]

    async def _get_available_keys(self, args: dict) -> list[TextContent]:
        """Handle get_available_keys tool."""
        key_type = args["key_type"]

        # Use async database connection
        async with Database(self.db_path) as db:
            engine = QueryEngine(db)
            keys = await engine.get_available_keys(key_type)

        import json

        return [TextContent(type="text", text=json.dumps(keys, indent=2))]

    async def _autocomplete_key(self, args: dict) -> list[TextContent]:
        """Handle autocomplete_key tool."""
        prefix = args["prefix"]
        key_type = args["key_type"]
        limit = args.get("limit", 20)

        # Use async database connection
        async with Database(self.db_path) as db:
            engine = QueryEngine(db)
            keys = await engine.autocomplete_key(prefix, key_type, limit)

        import json

        return [TextContent(type="text", text=json.dumps(keys, indent=2))]

    async def _index_file(self, args: dict) -> list[TextContent]:
        """Handle index_file tool."""
        file_path = Path(args["file_path"])
        force_reindex = args.get("force_reindex", False)

        scanner = HttpCatcherScanner.default()

        # Use async database connection
        async with Database(self.db_path) as db:
            indexer = Indexer(db, scanner)

            try:
                result = await indexer.index_file(file_path, force_reindex)
                import json

                return [TextContent(type="text", text=json.dumps(result, indent=2))]
            except Exception as e:
                import json

                return [
                    TextContent(
                        type="text", text=json.dumps({"error": str(e)}, indent=2)
                    )
                ]

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
                    self.server.create_initialization_options(),
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
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Create and run server
    server = HttpCatcherMCPServer(data_dir)
    server.run()
