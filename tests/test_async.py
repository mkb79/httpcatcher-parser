#!/usr/bin/env python3
"""Quick test of async modules."""

import asyncio
from src.httpcatcher_parser.mcp.database import Database


async def test_database():
    """Test async database connection."""
    print("Testing async database...")

    # Create in-memory database
    async with Database(":memory:") as db:
        await db.initialize(enable_fts=True)
        print("✓ Database initialized")

        # Test basic query
        conn = await db.connect()
        cursor = await conn.execute("SELECT COUNT(*) FROM session_files")
        row = await cursor.fetchone()
        count = row[0]
        print(f"✓ Session files count: {count}")

    print("✓ Database closed")


async def test_file_tracker():
    """Test async file tracker."""
    print("\nTesting async file tracker...")

    from src.httpcatcher_parser.mcp.file_tracker import FileTracker

    async with Database(":memory:") as db:
        await db.initialize(enable_fts=True)

        tracker = FileTracker(db)
        files = await tracker.list_files()
        print(f"✓ Listed {len(files)} files")


async def main():
    """Run all tests."""
    print("=== Async Module Tests ===\n")

    try:
        await test_database()
        await test_file_tracker()
        print("\n✓ All tests passed!")
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
