"""MCP server for HTTP Catcher session files.

This module requires the 'mcp' extra:
    pip install httpcatcher-parser[mcp]
    uv tool install "httpcatcher-parser[mcp]"
"""

try:
    import mcp
except ImportError as e:
    raise ImportError(
        "MCP server features require the 'mcp' extra. "
        "Install with: pip install httpcatcher-parser[mcp]"
    ) from e

__all__ = []
