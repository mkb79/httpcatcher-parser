#!/usr/bin/env python3
"""Test MCP server async integration."""

import asyncio
import json
import tempfile
from pathlib import Path
from src.httpcatcher_parser.mcp.server import HttpCatcherMCPServer
from src.httpcatcher_parser.mcp.database import Database


async def test_mcp_server():
    """Test that MCP server can be initialized and tools work."""
    print("=== Testing MCP Server Async Integration ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        db_path = data_dir / "index.db"

        # Initialize database
        async with Database(db_path) as db:
            await db.initialize(enable_fts=True)
        print("✓ Database initialized")

        # Create MCP server
        try:
            server = HttpCatcherMCPServer(data_dir)
            print("✓ MCP server created")
        except Exception as e:
            print(f"✗ Failed to create server: {e}")
            return False

        # Test list_files tool
        try:
            result = await server._list_files({"sort_by": "date"})
            print(f"✓ list_files works: {len(result)} files")
        except Exception as e:
            print(f"✗ list_files failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        # Test get_stats tool
        try:
            result = await server._get_stats({})
            print(f"✓ get_stats works: {result}")
        except Exception as e:
            print(f"✗ get_stats failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        # Test search_requests tool
        try:
            result = await server._search_requests({
                "limit": 10,
                "offset": 0
            })
            print(f"✓ search_requests works: {result[0]['count']} results")
        except Exception as e:
            print(f"✗ search_requests failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        # Test get_available_keys tool
        try:
            result = await server._get_available_keys({"key_type": "header"})
            print(f"✓ get_available_keys works: {len(result)} keys")
        except Exception as e:
            print(f"✗ get_available_keys failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        # Test autocomplete_key tool
        try:
            result = await server._autocomplete_key({
                "prefix": "Content",
                "key_type": "header",
                "limit": 10
            })
            print(f"✓ autocomplete_key works: {len(result)} matches")
        except Exception as e:
            print(f"✗ autocomplete_key failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    print("\n" + "="*60)
    print("SUCCESS: All MCP server tools are working with async!")
    print("="*60)
    print("\nThe MCP server is ready for Claude Desktop integration.")
    return True


if __name__ == "__main__":
    async def main():
        try:
            success = await test_mcp_server()
            return 0 if success else 1
        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return 1

    exit(asyncio.run(main()))
