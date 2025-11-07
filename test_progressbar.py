#!/usr/bin/env python3
"""Test progress bar functionality."""

import asyncio
import tempfile
from pathlib import Path
from tqdm.asyncio import tqdm as async_tqdm


async def test_progress_bar():
    """Test that tqdm progress bar works with asyncio."""
    print("=== Testing Progress Bar ===\n")

    async def mock_task(idx: int) -> tuple[bool, str, int, str]:
        """Mock file processing task."""
        await asyncio.sleep(0.2)  # Simulate work
        return True, f"{idx} requests", idx * 10, f"file_{idx}.hcs"

    # Create tasks
    tasks = [mock_task(i) for i in range(10)]

    # Process with progress bar
    results = []
    with async_tqdm(total=len(tasks), desc="Processing files", unit="file") as pbar:
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)

            success, message, req_count, filename = result
            status = "✓" if success else "✗"
            pbar.set_postfix_str(f"{status} {filename[:30]}")
            pbar.update(1)

    print("\n✓ Progress bar test completed!")
    print(f"Processed {len(results)} items")

    return True


if __name__ == "__main__":
    asyncio.run(test_progress_bar())
