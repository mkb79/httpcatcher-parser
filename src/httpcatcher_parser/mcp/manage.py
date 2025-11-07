"""Management CLI implementation for httpcatcher MCP server."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from .database import Database

DEFAULT_DATA_DIR = Path.home() / ".http_catcher"
DEFAULT_SESSIONS_DIR = DEFAULT_DATA_DIR / "sessions"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "index.db"
DEFAULT_CONFIG_PATH = DEFAULT_DATA_DIR / "config.json"


class Manager:
    """Manager for httpcatcher MCP server."""

    def __init__(self, data_dir: Optional[Path] = None):
        """Initialize manager.

        Args:
            data_dir: Base directory for data storage. Defaults to ~/.http_catcher
        """
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.sessions_dir = self.data_dir / "sessions"
        self.db_path = self.data_dir / "index.db"
        self.config_path = self.data_dir / "config.json"

        # Lazy initialization
        self._db: Optional[Database] = None
        self._config: Optional[dict] = None

    @property
    def db(self) -> Database:
        """Get database instance."""
        if self._db is None:
            self._db = Database(self.db_path)
        return self._db

    @property
    def config(self) -> dict:
        """Get configuration."""
        if self._config is None:
            self._config = self._load_config()
        return self._config

    def _load_config(self) -> dict:
        """Load configuration from file."""
        if self.config_path.exists():
            with open(self.config_path, 'r') as f:
                return json.load(f)
        return self._default_config()

    def _default_config(self) -> dict:
        """Get default configuration."""
        return {
            "data_dir": str(self.data_dir),
            "db_path": str(self.db_path),
            "sessions_dir": str(self.sessions_dir),
            "auto_index": True,
            "max_file_size_mb": 500,
            "log_level": "info",
            "server": {
                "port": None,
                "host": None
            },
            "indexing": {
                "batch_size": 1000,
                "parallel_workers": 4
            },
            "query": {
                "default_limit": 100,
                "max_limit": 10000
            }
        }

    def _save_config(self) -> None:
        """Save configuration to file."""
        with open(self.config_path, 'w') as f:
            json.dump(self.config, f, indent=2)

    # ====== INIT Command ======
    def cmd_server(self, args) -> None:
        """Start MCP server."""
        from .server import main as server_main

        # Check database exists
        if not self.db_path.exists():
            print(f"Error: Database not found: {self.db_path}")
            print("Run 'hc-mcp init' first")
            sys.exit(1)

        # Set log level
        log_level = getattr(args, 'log_level', 'info')

        # Override data dir if db-path provided
        data_dir = self.data_dir
        if hasattr(args, 'db_path') and args.db_path:
            data_dir = args.db_path.parent

        # Start server
        server_main(data_dir, log_level)

    def cmd_init(self, args) -> None:
        """Initialize directory structure and database."""
        print(f"Initializing httpcatcher MCP in: {self.data_dir}")

        # Create directories
        self.data_dir.mkdir(parents=True, exist_ok=True)
        print(f"✓ Created directory: {self.data_dir}")

        self.sessions_dir.mkdir(exist_ok=True)
        print(f"✓ Created subdirectory: {self.sessions_dir}")

        # Create logs directory
        logs_dir = self.data_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        print(f"✓ Created subdirectory: {logs_dir}")

        # Initialize database
        async def init_db():
            async with Database(self.db_path) as db:
                await db.initialize()

        asyncio.run(init_db())
        print(f"✓ Initialized database: {self.db_path}")

        # Create default config
        self._config = self._default_config()
        self._save_config()
        print(f"✓ Created config: {self.config_path}")

        print("\nSetup complete! You can now:")
        print("  - Add session files: hc-mcp files add <path>")
        print("  - Start server: hc-mcp server")

    # ====== DB Commands ======
    def cmd_db_status(self, args) -> None:
        """Show database status."""
        if not self.db_path.exists():
            print(f"Error: Database not found: {self.db_path}")
            print("Run 'hc-mcp init' first")
            sys.exit(1)

        async def get_stats():
            from .database_backup import Database as SyncDatabase
            db = SyncDatabase(self.db_path)
            return db.get_stats()

        stats = asyncio.run(get_stats())
        size_mb = self.db_path.stat().st_size / (1024 * 1024)

        print(f"Database: {self.db_path}")
        print(f"Status: OK")
        print(f"Size: {size_mb:.1f} MB")
        print(f"Total requests: {stats['total_requests']:,}")
        print(f"Total files: {stats['total_files']:,}")
        print(f"Total header keys: {stats['total_header_keys']:,}")
        print(f"Total cookie keys: {stats['total_cookie_keys']:,}")

    def cmd_db_vacuum(self, args) -> None:
        """Optimize database."""
        if not self.db_path.exists():
            print(f"Error: Database not found: {self.db_path}")
            sys.exit(1)

        size_before = self.db_path.stat().st_size

        print("Running VACUUM...")
        async def vacuum():
            async with Database(self.db_path) as db:
                await db.vacuum()
                await db.analyze()

        asyncio.run(vacuum())

        size_after = self.db_path.stat().st_size
        saved = size_before - size_after
        saved_pct = (saved / size_before * 100) if size_before > 0 else 0

        print("✓ Database optimized")
        print(f"  Before: {size_before / (1024*1024):.1f} MB")
        print(f"  After: {size_after / (1024*1024):.1f} MB")
        print(f"  Saved: {saved / (1024*1024):.1f} MB ({saved_pct:.1f}%)")

    def cmd_db_stats(self, args) -> None:
        """Show detailed database statistics."""
        if not self.db_path.exists():
            print(f"Error: Database not found: {self.db_path}")
            sys.exit(1)

        async def get_stats():
            from .database_backup import Database as SyncDatabase
            db = SyncDatabase(self.db_path)
            return db.get_stats()

        stats = asyncio.run(get_stats())

        print("┌─────────────────────┬────────┐")
        print("│ Metric              │ Value  │")
        print("├─────────────────────┼────────┤")
        print(f"│ Total Requests      │ {stats['total_requests']:,} │")
        print(f"│ Total Files         │ {stats['total_files']:,} │")
        print(f"│ Total Header Keys   │ {stats['total_header_keys']:,} │")
        print(f"│ Total Cookie Keys   │ {stats['total_cookie_keys']:,} │")
        print("└─────────────────────┴────────┘")

        # TODO: Add more detailed statistics (top hosts, status codes, etc.)

    def cmd_db_reset(self, args) -> None:
        """Reset database (delete all data)."""
        if not self.db_path.exists():
            print(f"Error: Database not found: {self.db_path}")
            sys.exit(1)

        if not args.confirm:
            print("⚠️  WARNING: This will delete all indexed data!")
            print(f"Session files in {self.sessions_dir} will NOT be deleted.")
            print()
            response = input("Continue? [y/N]: ")
            if response.lower() != 'y':
                print("Cancelled.")
                return

        async def reset():
            async with Database(self.db_path) as db:
                await db.reset()

        asyncio.run(reset())
        print("✓ Database reset complete")

    def cmd_db_migrate(self, args) -> None:
        """Run schema migrations."""
        # TODO: Implement migration system
        print("No migrations available")

    # ====== FILES Commands ======
    def cmd_files_list(self, args) -> None:
        """List all session files."""
        from .file_tracker import FileTracker
        from datetime import datetime

        if not self.db_path.exists():
            print(f"Error: Database not found: {self.db_path}")
            print("Run 'hc-mcp init' first")
            sys.exit(1)

        async def list_files():
            async with Database(self.db_path) as db:
                tracker = FileTracker(db)
                return await tracker.list_files()

        files = asyncio.run(list_files())

        if not files:
            print("No files indexed yet.")
            return

        if args.format == 'json':
            import json
            print(json.dumps(files, indent=2))
            return

        # Sort files
        if args.sort == 'name':
            files.sort(key=lambda f: f['filename'])
        elif args.sort == 'size':
            files.sort(key=lambda f: f['file_size'], reverse=True)
        elif args.sort == 'requests':
            files.sort(key=lambda f: f['request_count'], reverse=True)
        # 'date' is default (already sorted by indexed_at DESC)

        # Table format
        print(f"{'ID':<6} {'Filename':<30} {'Size':>10} {'Requests':>10} {'Indexed At':<20}")
        print("─" * 80)

        total_size = 0
        total_requests = 0

        for f in files:
            size_mb = f['file_size'] / (1024 * 1024)
            total_size += f['file_size']
            total_requests += f['request_count']

            # Format timestamp
            indexed_dt = datetime.fromtimestamp(f['indexed_at'])
            indexed_str = indexed_dt.strftime("%Y-%m-%d %H:%M")

            print(f"{f['id']:<6} {f['filename']:<30} {size_mb:>8.1f} MB {f['request_count']:>10,} {indexed_str:<20}")

        print("─" * 80)
        print(f"Total: {len(files)} files, {total_requests:,} requests, {total_size/(1024*1024):.1f} MB")

    def cmd_files_add(self, args) -> None:
        """Add a session file."""
        from ..hc_parser import HttpCatcherScanner
        from .indexer import Indexer
        from .file_tracker import FileTracker, compute_file_hash
        import shutil
        import os

        source_path = Path(args.path)

        if not source_path.exists():
            print(f"Error: File not found: {source_path}")
            sys.exit(1)

        if not self.db_path.exists():
            print(f"Error: Database not initialized")
            print("Run 'hc-mcp init' first")
            sys.exit(1)

        print(f"Adding: {source_path}")

        async def add_file():
            from tqdm import tqdm

            async with Database(self.db_path) as db:
                await db.initialize()

                # Check for duplicate by hash
                try:
                    with tqdm(total=1, desc="Computing hash", unit="file", leave=False) as pbar:
                        file_hash = await compute_file_hash(source_path)
                        pbar.update(1)

                    tracker = FileTracker(db)
                    duplicate = await tracker.find_duplicate_by_hash(file_hash)
                    if duplicate:
                        print(f"Error: File is a duplicate of already indexed file:")
                        print(f"  Existing: {duplicate['file_path']}")
                        print(f"  Hash: {file_hash}")
                        sys.exit(1)
                except Exception as e:
                    print(f"Warning: Could not check for duplicates: {e}")

                # Determine destination
                dest_name = args.name or source_path.name
                dest_path = self.sessions_dir / dest_name

                # Check if destination already exists
                if dest_path.exists() and dest_path != source_path:
                    print(f"Error: File already exists at destination: {dest_path}")
                    sys.exit(1)

                # Copy/Move/Link
                if source_path != dest_path:
                    with tqdm(total=1, desc="Copying file", unit="file", leave=False) as pbar:
                        if args.link:
                            os.symlink(source_path.resolve(), dest_path)
                            print(f"  ✓ Linked to: {dest_path}")
                        elif args.move:
                            shutil.move(str(source_path), str(dest_path))
                            print(f"  ✓ Moved to: {dest_path}")
                        else:  # copy (default)
                            shutil.copy2(source_path, dest_path)
                            print(f"  ✓ Copied to: {dest_path}")
                        pbar.update(1)
                else:
                    print(f"  ✓ File already in sessions directory")

                # Index
                scanner = HttpCatcherScanner.default()
                indexer = Indexer(db, scanner)

                try:
                    with tqdm(total=1, desc="Indexing file", unit="file", leave=False) as pbar:
                        result = await indexer.index_file(dest_path, force_reindex=True)
                        pbar.update(1)

                    print(f"  ✓ Indexed successfully")
                    print()
                    print(f"File ID: {result['file_id']}")
                    print(f"Requests added: {result['requests_added']}")
                except Exception as e:
                    print(f"  ✗ Indexing failed: {e}")
                    # Clean up on failure
                    if source_path != dest_path and dest_path.exists():
                        dest_path.unlink()
                    raise

        try:
            asyncio.run(add_file())
        except Exception:
            sys.exit(1)

    def cmd_files_add_dir(self, args) -> None:
        """Add all session files from directory with async parallel processing."""
        from ..hc_parser import HttpCatcherScanner
        from .indexer import Indexer
        from .file_tracker import FileTracker, compute_file_hash
        import shutil
        import os

        directory = Path(args.directory)

        if not directory.exists():
            print(f"Error: Directory not found: {directory}")
            sys.exit(1)

        if not self.db_path.exists():
            print(f"Error: Database not initialized")
            print("Run 'hc-mcp init' first")
            sys.exit(1)

        print(f"Scanning: {directory}")

        # Scan directory (sync - pathlib glob is efficient)
        async def scan():
            async with Database(self.db_path) as db:
                tracker = FileTracker(db)
                return tracker.scan_directory(directory, args.pattern, args.recursive)

        files = asyncio.run(scan())

        if not files:
            print(f"No files matching pattern '{args.pattern}' found.")
            return

        print(f"Found {len(files)} file(s)\n")

        # Process all files in parallel with async and progress bar
        async def process_all_files():
            from tqdm.asyncio import tqdm as async_tqdm

            async with Database(self.db_path) as db:
                await db.initialize()

                async def process_file(source_path: Path) -> tuple[bool, str, int, str]:
                    """Process a single file. Returns (success, message, request_count, filename)"""
                    filename = source_path.name
                    try:
                        # Check for duplicates by hash first
                        try:
                            file_hash = await compute_file_hash(source_path)
                            tracker = FileTracker(db)
                            duplicate = await tracker.find_duplicate_by_hash(file_hash)
                            if duplicate:
                                return False, f"duplicate (already indexed as {Path(duplicate['file_path']).name})", 0, filename
                        except Exception:
                            pass

                        dest_name = source_path.name
                        dest_path = self.sessions_dir / dest_name

                        # Check if destination file already exists
                        if dest_path.exists() and dest_path != source_path:
                            return False, "already exists in sessions directory", 0, filename

                        # Copy/Move/Link (sync file operations)
                        if source_path != dest_path:
                            if args.link:
                                os.symlink(source_path.resolve(), dest_path)
                            elif args.move:
                                shutil.move(str(source_path), str(dest_path))
                            else:
                                shutil.copy2(source_path, dest_path)

                        # Index (async - NO LOCK NEEDED!)
                        scanner = HttpCatcherScanner.default()
                        indexer = Indexer(db, scanner)
                        result = await indexer.index_file(dest_path, force_reindex=True)

                        return True, f"{result['requests_added']} requests", result['requests_added'], filename
                    except Exception as e:
                        return False, f"Error - {e}", 0, filename

                # Create tasks for all files
                tasks = [process_file(f) for f in files]

                # Process with progress bar using asyncio.as_completed for real-time updates
                results = []
                with async_tqdm(total=len(files), desc="Processing files", unit="file") as pbar:
                    for coro in asyncio.as_completed(tasks):
                        result = await coro
                        results.append(result)

                        # Update progress bar with current file info
                        success, message, req_count, filename = result
                        status = "✓" if success else "✗"
                        pbar.set_postfix_str(f"{status} {filename[:30]}")
                        pbar.update(1)

                return results

        # Run async processing
        results = asyncio.run(process_all_files())

        # Display results summary
        print()  # New line after progress bar
        success_count = 0
        failed_count = 0
        total_requests = 0
        errors = []

        for success, message, req_count, filename in results:
            if success:
                success_count += 1
                total_requests += req_count
            else:
                failed_count += 1
                errors.append(f"{filename}: {message}")

        # Show successful files
        print("Successful:")
        for success, message, req_count, filename in results:
            if success:
                print(f"  [✓] {filename}: {message}")

        # Show failed files
        if failed_count > 0:
            print("\nFailed:")
            for success, message, req_count, filename in results:
                if not success:
                    print(f"  [✗] {filename}: {message}")

        print(f"\nSummary:")
        print(f"  Added: {success_count} files")
        if failed_count > 0:
            print(f"  Skipped: {failed_count} files")
        print(f"  Total requests: {total_requests:,}")

    def cmd_files_remove(self, args) -> None:
        """Remove a session file."""
        from .file_tracker import FileTracker

        if not self.db_path.exists():
            print(f"Error: Database not found: {self.db_path}")
            sys.exit(1)

        tracker = FileTracker(self.db)
        file_id = tracker.resolve_file_id(args.identifier)

        if file_id is None:
            print(f"Error: File not found: {args.identifier}")
            sys.exit(1)

        file_info = tracker.get_file_info(file_id)

        print(f"Removing file: {file_info['filename']} (ID: {file_id})")

        # Remove from DB
        request_count = tracker.remove_file(file_id)
        print(f"  ✓ Removed {request_count} requests from database")
        print(f"  ✓ Removed file record")

        # Delete physical file
        if args.delete_file:
            file_path = Path(file_info['file_path'])
            if file_path.exists():
                file_path.unlink()
                print(f"  ✓ Deleted file: {file_path}")
        else:
            print()
            print(f"File still exists at: {file_info['file_path']}")
            print(f"To delete the file, use: hc-mcp files remove {file_id} --delete-file")

    def cmd_files_info(self, args) -> None:
        """Show detailed file information."""
        from .file_tracker import FileTracker
        from datetime import datetime

        if not self.db_path.exists():
            print(f"Error: Database not found: {self.db_path}")
            sys.exit(1)

        tracker = FileTracker(self.db)
        file_id = tracker.resolve_file_id(args.identifier)

        if file_id is None:
            print(f"Error: File not found: {args.identifier}")
            sys.exit(1)

        file_info = tracker.get_file_info(file_id)

        # Get additional statistics
        cursor = self.db.conn.execute(
            """
            SELECT
                status_code,
                COUNT(*) as count
            FROM requests
            WHERE file_id = ?
            GROUP BY status_code
            ORDER BY count DESC
            """,
            (file_id,)
        )
        status_breakdown = list(cursor.fetchall())

        cursor = self.db.conn.execute(
            """
            SELECT
                resp_content_category,
                COUNT(*) as count
            FROM requests
            WHERE file_id = ?
            GROUP BY resp_content_category
            ORDER BY count DESC
            """,
            (file_id,)
        )
        content_breakdown = list(cursor.fetchall())

        cursor = self.db.conn.execute(
            """
            SELECT
                MIN(req_timestamp) as first_ts,
                MAX(resp_timestamp) as last_ts
            FROM requests
            WHERE file_id = ?
            """,
            (file_id,)
        )
        time_range = cursor.fetchone()

        cursor = self.db.conn.execute(
            """
            SELECT host, COUNT(*) as count
            FROM requests
            WHERE file_id = ?
            GROUP BY host
            ORDER BY count DESC
            LIMIT 10
            """,
            (file_id,)
        )
        top_hosts = list(cursor.fetchall())

        # Print info
        print(f"File: {file_info['filename']}")
        print(f"Path: {file_info['file_path']}")
        print(f"ID: {file_info['id']}")
        print(f"Size: {file_info['file_size'] / (1024*1024):.1f} MB")
        print(f"SHA256: {file_info['file_hash'][:16]}...")
        print()
        indexed_dt = datetime.fromtimestamp(file_info['indexed_at'])
        print(f"Indexed: {indexed_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Requests: {file_info['request_count']}")
        print(f"Status: {file_info['status']}")

        if status_breakdown:
            print("\nStatus Code Breakdown:")
            for status, count in status_breakdown[:5]:
                status_str = f"{status}xx" if status is None else str(status)
                print(f"  {status_str}: {count}")

        if content_breakdown:
            print("\nContent Types:")
            for ct, count in content_breakdown[:5]:
                print(f"  {ct or 'unknown'}: {count}")

        if time_range and time_range[0] and time_range[1]:
            first_ts = datetime.fromtimestamp(time_range[0] / 1000)
            last_ts = datetime.fromtimestamp(time_range[1] / 1000)
            duration = (time_range[1] - time_range[0]) / 1000
            print("\nTime Range:")
            print(f"  First: {first_ts.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Last: {last_ts.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Duration: {duration:.1f}s")

        if top_hosts:
            print("\nTop Hosts:")
            for host, count in top_hosts:
                print(f"  {host}: {count} requests")

    def cmd_files_reindex(self, args) -> None:
        """Reindex session file(s) with parallel processing."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..hc_parser import HttpCatcherScanner
        from .indexer import Indexer
        from .file_tracker import FileTracker

        if not self.db_path.exists():
            print(f"Error: Database not found: {self.db_path}")
            sys.exit(1)

        tracker = FileTracker(self.db)
        scanner = HttpCatcherScanner.default()

        # Determine files to reindex
        if args.all:
            files_to_reindex = tracker.list_files()
            if not files_to_reindex:
                print("No files to reindex.")
                return
            print(f"Reindexing {len(files_to_reindex)} file(s)...")
        else:
            if not args.identifier:
                print("Error: Must specify file ID/name or --all")
                sys.exit(1)

            file_id = tracker.resolve_file_id(args.identifier)
            if file_id is None:
                print(f"Error: File not found: {args.identifier}")
                sys.exit(1)

            file_info = tracker.get_file_info(file_id)
            files_to_reindex = [file_info]
            print(f"Reindexing: {file_info['filename']}")

        # Reindex in parallel
        def reindex_file(file_info: dict) -> tuple[bool, str, int]:
            try:
                indexer = Indexer(self.db, scanner)
                file_path = Path(file_info['file_path'])

                if not file_path.exists():
                    return False, f"{file_info['filename']}: File not found", 0

                with db_lock:
                    result = indexer.index_file(file_path, force_reindex=True)
                return True, f"{file_info['filename']}: {result['requests_added']} requests", result['requests_added']
            except Exception as e:
                return False, f"{file_info['filename']}: Error - {e}", 0

        # Use CPU count * 2 for I/O bound tasks
        import os
        from threading import Lock
        default_workers = min(os.cpu_count() * 2 if os.cpu_count() else 8, 16)
        max_workers = self.config.get('indexing', {}).get('parallel_workers', default_workers)

        # Lock for database operations
        db_lock = Lock()

        success_count = 0
        failed_count = 0
        total_requests = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(reindex_file, f): f for f in files_to_reindex}

            for future in as_completed(futures):
                success, message, req_count = future.result()
                status = "✓" if success else "✗"
                print(f"  [{status}] {message}")

                if success:
                    success_count += 1
                    total_requests += req_count
                else:
                    failed_count += 1

        print(f"\nSummary:")
        print(f"  Success: {success_count}")
        print(f"  Failed: {failed_count}")
        print(f"  Total requests: {total_requests:,}")

    def cmd_files_check(self, args) -> None:
        """Check consistency between DB and filesystem."""
        from ..hc_parser import HttpCatcherScanner
        from .indexer import Indexer
        from .file_tracker import FileTracker

        if not self.db_path.exists():
            print(f"Error: Database not found: {self.db_path}")
            sys.exit(1)

        print("Checking consistency...")
        tracker = FileTracker(self.db)
        issues = tracker.check_consistency()

        if not any(issues.values()):
            print("✓ No issues found")
            return

        print("\nIssues found:\n")

        # Orphaned
        if issues['orphaned']:
            print("Orphaned (in DB, but file missing):")
            for f in issues['orphaned']:
                print(f"  - {f['filename']} (ID: {f['id']}, {f['request_count']} requests)")
            print()

        # Modified
        if issues['modified']:
            print("Modified (file changed):")
            for f in issues['modified']:
                print(f"  - {f['filename']} (ID: {f['id']})")
                print(f"    DB Hash: {f['db_hash'][:12]}...")
                print(f"    File Hash: {f['file_hash'][:12]}...")
            print()

        print("Summary:")
        print(f"  Orphaned: {len(issues['orphaned'])} files")
        print(f"  Modified: {len(issues['modified'])} files")
        print()
        print("Run with --fix to automatically fix these issues.")

        if args.fix:
            print("\nFixing issues...")

            # Remove orphaned
            for f in issues['orphaned']:
                tracker.remove_file(f['id'])
                print(f"  ✓ Removed orphaned: {f['filename']}")

            # Reindex modified
            if issues['modified']:
                scanner = HttpCatcherScanner.default()
                indexer = Indexer(self.db, scanner)

                for f in issues['modified']:
                    try:
                        file_path = Path(f['file_path'])
                        result = indexer.index_file(file_path, force_reindex=True)
                        print(f"  ✓ Reindexed: {f['filename']} ({result['requests_added']} requests)")
                    except Exception as e:
                        print(f"  ✗ Failed to reindex {f['filename']}: {e}")

            print("\n✓ Issues fixed")

    # ====== CONFIG Commands ======
    def cmd_config_get(self, args) -> None:
        """Get configuration value(s)."""
        if not self.config_path.exists():
            print(f"Error: Config not found: {self.config_path}")
            print("Run 'hc-mcp init' first")
            sys.exit(1)

        if args.key:
            # Get specific key
            value = self.config.get(args.key, f"<key '{args.key}' not found>")
            print(f"{args.key}: {value}")
        else:
            # Show all config
            for key, value in self.config.items():
                print(f"{key}: {value}")

    def cmd_config_set(self, args) -> None:
        """Set configuration value."""
        if not self.config_path.exists():
            print(f"Error: Config not found: {self.config_path}")
            print("Run 'hc-mcp init' first")
            sys.exit(1)

        # Update config
        self.config[args.key] = args.value
        self._save_config()
        print(f"✓ Updated {args.key} = {args.value}")

    # ====== SEARCH Commands ======
    def cmd_search(self, args) -> None:
        """Search requests with filters."""
        from .query_engine import QueryEngine, SearchFilters, HeaderFilter, CookieFilter, MatchMode

        if not self.db:
            print("Database not initialized")
            sys.exit(1)

        # Build filters from args
        filters = SearchFilters(
            method=args.method,
            url=args.url,
            host=args.host,
            path=args.path,
            limit=args.limit,
            offset=args.offset,
            sort_desc=not args.asc
        )

        # Map sort options
        sort_map = {
            'time': 'req_timestamp',
            'duration': 'duration_ms',
            'status': 'status_code'
        }
        filters.sort_by = sort_map.get(args.sort, 'req_timestamp')

        # Status filters
        if args.status:
            filters.status_codes = [args.status]
        if args.status_min is not None:
            filters.status_min = args.status_min
        if args.status_max is not None:
            filters.status_max = args.status_max

        # Header filters
        if args.header:
            for h in args.header:
                if ':' in h:
                    key, value = h.split(':', 1)
                    hf = HeaderFilter(
                        key=key if key else None,
                        value=value if value else None
                    )
                    filters.headers.append(hf)

        # Cookie filters
        if args.cookie:
            for c in args.cookie:
                if ':' in c:
                    key, value = c.split(':', 1)
                    cf = CookieFilter(
                        key=key if key else None,
                        value=value if value else None
                    )
                    filters.cookies.append(cf)

        # Body search
        if args.body:
            filters.body_search = args.body

        # Execute search
        engine = QueryEngine(self.db)
        results = engine.search_requests(filters)
        total = engine.count_requests(filters)

        if args.format == 'json':
            import json
            output = {
                'total': total,
                'count': len(results),
                'offset': args.offset,
                'limit': args.limit,
                'results': results
            }
            print(json.dumps(output, indent=2))
        else:
            # Table format
            if not results:
                print(f"No results found (total: {total})")
                return

            print(f"\nFound {total} total requests, showing {len(results)}:\n")

            # Print table header
            print(f"{'ID':<8} {'Method':<8} {'Status':<8} {'Host':<30} {'Path':<40}")
            print("=" * 100)

            # Print rows
            for r in results:
                req_id = str(r['id'])
                method = r['method'] or 'N/A'
                status = str(r['status_code']) if r['status_code'] else 'N/A'
                host = (r['host'] or 'N/A')[:29]
                path = (r['path'] or 'N/A')[:39]

                print(f"{req_id:<8} {method:<8} {status:<8} {host:<30} {path:<40}")

            print(f"\nShowing {args.offset + 1}-{args.offset + len(results)} of {total}")

    # ====== STATS Commands ======
    def cmd_stats(self, args) -> None:
        """Show statistics."""
        from .query_engine import QueryEngine
        from .file_tracker import FileTracker

        if not self.db:
            print("Database not initialized")
            sys.exit(1)

        # Resolve file ID if specified
        file_id = None
        if args.file:
            tracker = FileTracker(self.db)
            file_id = tracker.resolve_file_id(args.file)
            if not file_id:
                print(f"File not found: {args.file}")
                sys.exit(1)

        engine = QueryEngine(self.db)
        stats = engine.get_stats(file_id)

        if args.format == 'json':
            import json
            print(json.dumps(stats, indent=2))
        else:
            # Table format
            print("\n=== HTTP Request Statistics ===\n")

            print(f"Total Requests: {stats['total_requests']}\n")

            # By method
            if stats['by_method']:
                print("Requests by Method:")
                for method, count in stats['by_method'].items():
                    print(f"  {method or 'N/A':<10} {count:>6}")
                print()

            # By status code
            if stats['by_status']:
                print("Top Status Codes:")
                for status, count in list(stats['by_status'].items())[:10]:
                    print(f"  {status or 'N/A':<10} {count:>6}")
                print()

            # By content category
            if stats['by_content_category']:
                print("Content Categories:")
                for cat, count in stats['by_content_category'].items():
                    print(f"  {cat or 'N/A':<15} {count:>6}")
                print()

            # Top hosts
            if stats['top_hosts']:
                print("Top Hosts:")
                for host, count in stats['top_hosts'].items():
                    host_display = (host or 'N/A')[:50]
                    print(f"  {host_display:<50} {count:>6}")
                print()

            # Time range
            if stats['time_range']['start']:
                from datetime import datetime
                start = datetime.fromtimestamp(stats['time_range']['start'])
                end = datetime.fromtimestamp(stats['time_range']['end'])
                print(f"Time Range: {start} to {end}\n")

            # Duration stats
            if stats['duration']['avg_ms'] is not None:
                print("Duration Statistics:")
                print(f"  Average: {stats['duration']['avg_ms']:.2f} ms")
                print(f"  Min: {stats['duration']['min_ms']} ms")
                print(f"  Max: {stats['duration']['max_ms']} ms")
                print()

            # Body sizes
            print("Body Sizes:")
            print(f"  Total Request: {stats['body_sizes']['total_req_bytes']:,} bytes")
            print(f"  Total Response: {stats['body_sizes']['total_resp_bytes']:,} bytes")
            print(f"  Avg Request: {stats['body_sizes']['avg_req_bytes']:.2f} bytes")
            print(f"  Avg Response: {stats['body_sizes']['avg_resp_bytes']:.2f} bytes")

    # ====== KEYS Commands ======
    def cmd_keys(self, args) -> None:
        """List available header/cookie keys."""
        from .query_engine import QueryEngine

        if not self.db:
            print("Database not initialized")
            sys.exit(1)

        engine = QueryEngine(self.db)

        if args.prefix:
            # Autocomplete mode
            keys = engine.autocomplete_key(args.prefix, args.type, args.limit)
            print(f"\nKeys matching '{args.prefix}*':")
            for key in keys:
                print(f"  {key}")
        else:
            # List all mode
            keys = engine.get_available_keys(args.type)
            if args.limit:
                keys = keys[:args.limit]

            print(f"\nAvailable {args.type} keys:\n")
            print(f"{'Key Name':<50} {'Usage Count':<15}")
            print("=" * 65)

            for k in keys:
                print(f"{k['name']:<50} {k['usage_count']:<15}")

            print(f"\nTotal: {len(keys)} keys")

    # ====== DETAILS Commands ======
    def cmd_details(self, args) -> None:
        """Show request details."""
        from .detail_fetcher import DetailFetcher, DetailLevel

        if not self.db:
            print("Database not initialized")
            sys.exit(1)

        # Map CLI level to enum
        level_map = {
            'full': DetailLevel.FULL,
            'headers': DetailLevel.HEADERS_ONLY,
            'request': DetailLevel.REQUEST_ONLY,
            'response': DetailLevel.RESPONSE_ONLY,
            'metadata': DetailLevel.METADATA
        }
        detail_level = level_map.get(args.level, DetailLevel.FULL)

        fetcher = DetailFetcher(self.db)
        details = fetcher.get_request_details(
            args.request_id,
            detail_level,
            args.decompress
        )

        if not details:
            print(f"Request not found: {args.request_id}")
            sys.exit(1)

        if args.format == 'json':
            import json
            # Convert bytes to base64 for JSON serialization
            def make_json_safe(obj):
                if isinstance(obj, bytes):
                    import base64
                    return base64.b64encode(obj).decode('ascii')
                elif isinstance(obj, dict):
                    return {k: make_json_safe(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [make_json_safe(item) for item in obj]
                return obj

            print(json.dumps(make_json_safe(details), indent=2))
        else:
            # Pretty format
            from datetime import datetime

            print("\n" + "=" * 80)
            print(f"REQUEST DETAILS - ID {details['id']}")
            print("=" * 80)

            # Basic info
            print(f"\nMethod: {details['method']}")
            print(f"URL: {details['url']}")
            print(f"Host: {details['host']}")
            print(f"Path: {details['path']}")
            print(f"Status: {details['status_code']}")

            if details.get('req_timestamp'):
                req_time = datetime.fromtimestamp(details['req_timestamp'])
                print(f"Request Time: {req_time}")

            if details.get('resp_timestamp'):
                resp_time = datetime.fromtimestamp(details['resp_timestamp'])
                print(f"Response Time: {resp_time}")

            if details.get('duration_ms') is not None:
                print(f"Duration: {details['duration_ms']} ms")

            # Request section
            if 'request' in details:
                print("\n" + "-" * 80)
                print("REQUEST")
                print("-" * 80)

                print(f"\nContent-Type: {details.get('req_content_type', 'N/A')}")
                print(f"Body Size: {details['request']['body_size']} bytes")

                print("\nHeaders:")
                print(fetcher.format_headers(details['request']['headers']))

                if details['request']['cookies']:
                    print("\nCookies:")
                    print(fetcher.format_cookies(details['request']['cookies']))

                if 'body' in details['request']:
                    print("\nBody:")
                    body = details['request']['body']
                    if isinstance(body, bytes):
                        # Try to decode
                        try:
                            body_str = body.decode('utf-8', errors='replace')
                            if len(body_str) > 1000:
                                body_str = body_str[:1000] + f"\n... ({len(body) - 1000} more bytes)"
                            print(body_str)
                        except Exception:
                            print(f"(binary data, {len(body)} bytes)")
                    else:
                        print(body)
                elif details['request'].get('body_preview'):
                    print("\nBody Preview:")
                    print(details['request']['body_preview'])

            # Response section
            if 'response' in details:
                print("\n" + "-" * 80)
                print("RESPONSE")
                print("-" * 80)

                print(f"\nContent-Type: {details.get('resp_content_type', 'N/A')}")
                print(f"Content Category: {details.get('resp_content_category', 'N/A')}")
                print(f"Body Size: {details['response']['body_size']} bytes")

                print("\nHeaders:")
                print(fetcher.format_headers(details['response']['headers']))

                if details['response']['cookies']:
                    print("\nSet-Cookie:")
                    print(fetcher.format_cookies(details['response']['cookies']))

                if 'body' in details['response']:
                    print("\nBody:")
                    body = details['response']['body']
                    if isinstance(body, bytes):
                        # Try to decode
                        try:
                            body_str = body.decode('utf-8', errors='replace')
                            if len(body_str) > 1000:
                                body_str = body_str[:1000] + f"\n... ({len(body) - 1000} more bytes)"
                            print(body_str)
                        except Exception:
                            print(f"(binary data, {len(body)} bytes)")
                    else:
                        print(body)
                elif details['response'].get('body_preview'):
                    print("\nBody Preview:")
                    print(details['response']['body_preview'])

            print("\n" + "=" * 80)
