"""Management CLI implementation for httpcatcher MCP server."""

from __future__ import annotations

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
        self.db.initialize()
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

        stats = self.db.get_stats()
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
        self.db.vacuum()

        print("Running ANALYZE...")
        self.db.analyze()

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

        stats = self.db.get_stats()

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

        self.db.reset()
        print("✓ Database reset complete")

    def cmd_db_migrate(self, args) -> None:
        """Run schema migrations."""
        # TODO: Implement migration system
        print("No migrations available")

    # ====== FILES Commands (Placeholders) ======
    def cmd_files_list(self, args) -> None:
        """List all session files."""
        # TODO: Implement in Milestone 3
        print("Not implemented yet (Milestone 3)")
        sys.exit(1)

    def cmd_files_add(self, args) -> None:
        """Add a session file."""
        from ..hc_parser import HttpCatcherScanner
        from .indexer import Indexer

        source_path = Path(args.path)

        if not source_path.exists():
            print(f"Error: File not found: {source_path}")
            sys.exit(1)

        if not self.db_path.exists():
            print(f"Error: Database not initialized")
            print("Run 'hc-mcp init' first")
            sys.exit(1)

        print(f"Adding: {source_path}")

        # Determine destination
        dest_name = args.name or source_path.name
        dest_path = self.sessions_dir / dest_name

        # Check if destination already exists
        if dest_path.exists() and dest_path != source_path:
            print(f"Error: File already exists: {dest_path}")
            sys.exit(1)

        # Copy/Move/Link
        if source_path != dest_path:
            if args.link:
                import os
                os.symlink(source_path.resolve(), dest_path)
                print(f"  ✓ Linked to: {dest_path}")
            elif args.move:
                import shutil
                shutil.move(str(source_path), str(dest_path))
                print(f"  ✓ Moved to: {dest_path}")
            else:  # copy (default)
                import shutil
                shutil.copy2(source_path, dest_path)
                print(f"  ✓ Copied to: {dest_path}")
        else:
            print(f"  ✓ File already in sessions directory")

        # Index
        print("  ✓ Indexing...")
        scanner = HttpCatcherScanner.default()
        indexer = Indexer(self.db, scanner)

        try:
            result = indexer.index_file(dest_path, force_reindex=True)
            print(f"    Found: {result['requests_added']} requests")
            print(f"  ✓ Indexed successfully")
            print()
            print(f"File ID: {result['file_id']}")
            print(f"Requests added: {result['requests_added']}")
        except Exception as e:
            print(f"  ✗ Indexing failed: {e}")
            # Clean up on failure
            if source_path != dest_path and dest_path.exists():
                dest_path.unlink()
            sys.exit(1)

    def cmd_files_add_dir(self, args) -> None:
        """Add all session files from directory."""
        # TODO: Implement in Milestone 3
        print("Not implemented yet (Milestone 3)")
        sys.exit(1)

    def cmd_files_remove(self, args) -> None:
        """Remove a session file."""
        # TODO: Implement in Milestone 3
        print("Not implemented yet (Milestone 3)")
        sys.exit(1)

    def cmd_files_info(self, args) -> None:
        """Show detailed file information."""
        # TODO: Implement in Milestone 3
        print("Not implemented yet (Milestone 3)")
        sys.exit(1)

    def cmd_files_reindex(self, args) -> None:
        """Reindex session file(s)."""
        # TODO: Implement in Milestone 3
        print("Not implemented yet (Milestone 3)")
        sys.exit(1)

    def cmd_files_check(self, args) -> None:
        """Check consistency between DB and filesystem."""
        # TODO: Implement in Milestone 3
        print("Not implemented yet (Milestone 3)")
        sys.exit(1)

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
