#!/usr/bin/env python3
"""Test race condition fix in KeyIndexer."""

import asyncio
import tempfile
from pathlib import Path
from src.httpcatcher_parser.mcp.database import Database
from src.httpcatcher_parser.mcp.indexer import KeyIndexer


async def test_concurrent_key_creation():
    """Test that concurrent key creation doesn't cause UNIQUE constraint errors."""
    print("=== Testing Concurrent Key Creation ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        async with Database(db_path) as db:
            await db.initialize(enable_fts=True)
            print("✓ Database initialized")

            # Create multiple indexers (simulating parallel file processing)
            indexer1 = KeyIndexer(db, prefetch=False)
            indexer2 = KeyIndexer(db, prefetch=False)
            indexer3 = KeyIndexer(db, prefetch=False)

            # All try to create the same header keys concurrently
            test_keys = [
                "Content-Type",
                "Authorization",
                "User-Agent",
                "Accept",
                "Cookie",
            ]

            print(
                f"✓ Testing concurrent creation of {len(test_keys)} keys from 3 indexers..."
            )

            # All 3 indexers try to get/create the same keys at the same time
            tasks = []
            for key in test_keys:
                tasks.append(indexer1.get_or_create_header_key(key))
                tasks.append(indexer2.get_or_create_header_key(key))
                tasks.append(indexer3.get_or_create_header_key(key))

            # Execute all concurrently - this would cause race conditions before the fix
            try:
                results = await asyncio.gather(*tasks)
                print(
                    f"✓ Successfully created/retrieved {len(results)} key IDs concurrently"
                )
            except Exception as e:
                print(f"✗ FAILED: {e}")
                import traceback

                traceback.print_exc()
                return False

            # Verify all keys were created correctly
            conn = await db.connect()
            cursor = await conn.execute("SELECT COUNT(*) FROM header_keys")
            row = await cursor.fetchone()
            count = row[0]

            if count != len(test_keys):
                print(f"✗ FAILED: Expected {len(test_keys)} keys, got {count}")
                return False

            print(f"✓ Database has exactly {count} unique keys (correct!)")

            # Verify all indexers got the same IDs for the same keys
            for key in test_keys:
                id1 = await indexer1.get_or_create_header_key(key)
                id2 = await indexer2.get_or_create_header_key(key)
                id3 = await indexer3.get_or_create_header_key(key)

                if id1 != id2 or id2 != id3:
                    print(
                        f"✗ FAILED: Key '{key}' has different IDs: {id1}, {id2}, {id3}"
                    )
                    return False

            print("✓ All indexers agree on key IDs")

            # Test cookie keys as well
            print("\n✓ Testing concurrent cookie key creation...")
            cookie_keys = ["session_id", "csrf_token", "auth", "preferences"]

            tasks = []
            for key in cookie_keys:
                tasks.append(indexer1.get_or_create_cookie_key(key))
                tasks.append(indexer2.get_or_create_cookie_key(key))
                tasks.append(indexer3.get_or_create_cookie_key(key))

            try:
                results = await asyncio.gather(*tasks)
                print(
                    f"✓ Successfully created/retrieved {len(results)} cookie key IDs concurrently"
                )
            except Exception as e:
                print(f"✗ FAILED: {e}")
                return False

            cursor = await conn.execute("SELECT COUNT(*) FROM cookie_keys")
            row = await cursor.fetchone()
            count = row[0]

            if count != len(cookie_keys):
                print(f"✗ FAILED: Expected {len(cookie_keys)} cookie keys, got {count}")
                return False

            print(f"✓ Database has exactly {count} unique cookie keys (correct!)")

    print("\n" + "=" * 60)
    print("SUCCESS: Race condition fix works correctly!")
    print("=" * 60)
    print("\nThe KeyIndexer can now safely handle concurrent key creation")
    print("across multiple parallel file processing tasks.")
    return True


if __name__ == "__main__":

    async def main():
        try:
            success = await test_concurrent_key_creation()
            return 0 if success else 1
        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return 1

    exit(asyncio.run(main()))
