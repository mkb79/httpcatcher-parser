# Async Migration Status

## Completed ✅

All core MCP modules have been converted to async:

### 1. Database Layer
- **File**: `database_async.py`
- **Class**: `AsyncDatabase`
- **Features**:
  - Async connection management with `aiosqlite`
  - All database operations use `await`
  - Context manager support (`async with`)
  - Schema initialization and management

### 2. Indexer
- **File**: `indexer_async.py`
- **Classes**: `AsyncIndexer`, `AsyncKeyIndexer`
- **Features**:
  - Async file parsing and database insertion
  - Key prefetching for performance
  - Two-phase insert for auto-increment ID mapping
  - Bulk insert operations

### 3. File Tracker
- **File**: `file_tracker_async.py`
- **Class**: `AsyncFileTracker`
- **Features**:
  - Async file hash computation with `aiofiles`
  - Async file metadata tracking
  - Duplicate detection
  - Consistency checking

### 4. Query Engine
- **File**: `query_engine_async.py`
- **Class**: `AsyncQueryEngine`
- **Features**:
  - Async search with complex filters
  - Async statistics gathering
  - Header/cookie key autocomplete
  - All queries use async iteration

### 5. Detail Fetcher
- **File**: `detail_fetcher_async.py`
- **Class**: `AsyncDetailFetcher`
- **Features**:
  - Async body loading with `aiofiles`
  - Lazy loading support
  - Decompression (sync - no I/O)
  - Multiple detail levels

## Integration with CLI

The CLI layer (`manage.py`, `cli.py`) can use these async modules with `asyncio.run()`:

### Simple Pattern

```python
import asyncio
from .database_async import AsyncDatabase
from .file_tracker_async import AsyncFileTracker

def cmd_files_list(self, args):
    """List all session files."""
    async def async_list():
        async with AsyncDatabase(self.db_path) as db:
            tracker = AsyncFileTracker(db)
            return await tracker.list_files()

    files = asyncio.run(async_list())
    # ... format and display results
```

### Parallel Processing Pattern

For operations like `cmd_files_add_dir` that process multiple files, use `asyncio.gather()`:

```python
import asyncio
from pathlib import Path

async def process_files_async(files: list[Path], db_path: Path, sessions_dir: Path):
    """Process multiple files in parallel using async."""
    from .database_async import AsyncDatabase
    from .indexer_async import AsyncIndexer
    from .file_tracker_async import AsyncFileTracker, compute_file_hash
    from ..hc_parser import HttpCatcherScanner
    import shutil

    async with AsyncDatabase(db_path) as db:
        await db.initialize()

        async def process_file(source_path: Path):
            try:
                # Check for duplicates
                file_hash = await compute_file_hash(source_path)
                tracker = AsyncFileTracker(db)
                duplicate = await tracker.find_duplicate_by_hash(file_hash)
                if duplicate:
                    return False, f"duplicate: {source_path.name}", 0

                # Copy to sessions directory
                dest_path = sessions_dir / source_path.name
                if source_path != dest_path:
                    shutil.copy2(source_path, dest_path)

                # Index the file
                scanner = HttpCatcherScanner.default()
                indexer = AsyncIndexer(db, scanner)
                result = await indexer.index_file(dest_path, force_reindex=True)

                return True, source_path.name, result['requests_added']
            except Exception as e:
                return False, f"error: {source_path.name}: {e}", 0

        # Process all files in parallel
        results = await asyncio.gather(*[process_file(f) for f in files])
        return results

# In CLI command:
def cmd_files_add_dir(self, args):
    files = [...] # scan for files
    results = asyncio.run(process_files_async(files, self.db_path, self.sessions_dir))
    # ... display results
```

## Performance Benefits

### With Threading (Before)
- Database locking required (`threading.Lock()`)
- GIL contention
- Context switching overhead
- Thread pool management

### With Async (After)
- No locking needed (single-threaded)
- No GIL issues
- Lightweight coroutines
- True concurrency for I/O operations
- Better resource utilization

### Benchmarks
On 25 session files:
- **Threading + Lock**: ~X seconds (with occasional failures)
- **Async/aiosqlite**: ~Y seconds (expected improvement: 20-40%)

## Next Steps

### 1. CLI Integration (High Priority)
Convert these commands to use async modules:
- [x] `db status` - Simple, use `asyncio.run()`
- [x] `db vacuum` - Simple, use `asyncio.run()`
- [x] `db stats` - Simple, use `asyncio.run()`
- [x] `db reset` - Simple, use `asyncio.run()`
- [x] `files list` - Simple, use `asyncio.run()`
- [x] `files add` - Medium, single file indexing
- [x] `files add-dir` - Complex, parallel processing with `asyncio.gather()`
- [x] `files remove` - Simple, use `asyncio.run()`
- [x] `files info` - Simple, use `asyncio.run()`
- [x] `files reindex` - Medium, can be single or multiple files
- [x] `files check` - Medium, consistency checking
- [x] `search` - Medium, use `asyncio.run()`
- [x] `stats` - Simple, use `asyncio.run()`
- [x] `keys` - Simple, use `asyncio.run()`
- [x] `details` - Simple, use `asyncio.run()`

### 2. MCP Server Integration (High Priority)
The MCP server (`server.py`) needs to be updated to use async modules:
- Read `server.py` to understand current structure
- Convert tool handlers to async
- Use `AsyncDatabase` instead of `Database`
- Test all 7 MCP tools

### 3. Testing (High Priority)
- Test all CLI commands with async modules
- Test MCP server with async modules
- Verify no threading issues
- Benchmark performance improvements
- Test with 25+ files again

### 4. Cleanup (Low Priority)
- Remove old sync modules or mark as deprecated
- Update documentation
- Add async examples to README
- Consider removing threading code entirely

## Migration Decision

**Current approach**: Full async migration for core modules ✅

This provides:
- Clean async/await syntax
- No threading complexity
- No locking required
- Better scalability
- Modern Python patterns

The CLI layer wraps async operations with `asyncio.run()`, keeping the CLI code simple while getting all the benefits of async I/O in the core modules.
