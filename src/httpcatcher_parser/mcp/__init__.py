"""MCP server for HTTP Catcher session files.

This module requires the 'mcp' extra:
    pip install httpcatcher-parser[mcp]
    uv tool install "httpcatcher-parser[mcp]"
"""

import importlib.util

if importlib.util.find_spec("mcp") is None:
    raise ImportError(
        "MCP server features require the 'mcp' extra. "
        "Install with: pip install httpcatcher-parser[mcp]"
    )

__all__ = []
