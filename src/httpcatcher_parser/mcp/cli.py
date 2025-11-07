"""Main CLI for httpcatcher MCP."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .manage import Manager, DEFAULT_DATA_DIR


def main():
    """Main entry point for hc-mcp CLI."""
    parser = argparse.ArgumentParser(
        prog="hc-mcp",
        description="HTTP Catcher MCP Server - Management and Query Tool"
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Data directory (default: {DEFAULT_DATA_DIR})"
    )

    subparsers = parser.add_subparsers(dest='command', required=True)

    # ========================================================================
    # INIT Command
    # ========================================================================
    subparsers.add_parser(
        'init',
        help="Initialize directory structure and database"
    )

    # ========================================================================
    # SERVER Command
    # ========================================================================
    server_parser = subparsers.add_parser(
        'server',
        help="Start MCP server (stdio mode)"
    )
    server_parser.add_argument(
        '--config',
        type=Path,
        help="Path to config file"
    )
    server_parser.add_argument(
        '--log-level',
        choices=['debug', 'info', 'warning', 'error'],
        default='info',
        help="Logging level"
    )
    server_parser.add_argument(
        '--db-path',
        type=Path,
        help="Override database path"
    )

    # ========================================================================
    # DB Commands
    # ========================================================================
    db_parser = subparsers.add_parser('db', help="Database management")
    db_sub = db_parser.add_subparsers(dest='db_command', required=True)

    db_sub.add_parser('status', help="Show database status")
    db_sub.add_parser('vacuum', help="Optimize database (VACUUM + ANALYZE)")
    db_sub.add_parser('stats', help="Detailed statistics")

    reset_parser = db_sub.add_parser('reset', help="Reset database (delete all data)")
    reset_parser.add_argument('--confirm', action='store_true', help="Skip confirmation")

    db_sub.add_parser('migrate', help="Run schema migrations")

    # ========================================================================
    # FILES Commands
    # ========================================================================
    files_parser = subparsers.add_parser('files', help="Session file management")
    files_sub = files_parser.add_subparsers(dest='files_command', required=True)

    # files list
    list_parser = files_sub.add_parser('list', help="List all session files")
    list_parser.add_argument('--format', choices=['table', 'json'], default='table')
    list_parser.add_argument('--sort', choices=['name', 'date', 'size', 'requests'], default='date')

    # files add
    add_parser = files_sub.add_parser('add', help="Add a session file")
    add_parser.add_argument('path', type=Path, help="Path to session file")
    add_parser.add_argument('--name', help="Custom filename")
    add_group = add_parser.add_mutually_exclusive_group()
    add_group.add_argument('--copy', action='store_true', default=True, help="Copy file (default)")
    add_group.add_argument('--move', action='store_true', help="Move file")
    add_group.add_argument('--link', action='store_true', help="Create symlink")

    # files add-dir
    adddir_parser = files_sub.add_parser('add-dir', help="Add all session files from directory")
    adddir_parser.add_argument('directory', type=Path, help="Directory path")
    adddir_parser.add_argument('--recursive', action='store_true', help="Scan recursively")
    adddir_parser.add_argument('--pattern', default='*.session', help="File pattern (default: *.session)")
    adddir_group = adddir_parser.add_mutually_exclusive_group()
    adddir_group.add_argument('--copy', action='store_true', default=True)
    adddir_group.add_argument('--move', action='store_true')
    adddir_group.add_argument('--link', action='store_true')

    # files remove
    remove_parser = files_sub.add_parser('remove', help="Remove a session file")
    remove_parser.add_argument('identifier', help="File ID or filename")
    remove_parser.add_argument('--delete-file', action='store_true', help="Also delete physical file")

    # files info
    info_parser = files_sub.add_parser('info', help="Show detailed file information")
    info_parser.add_argument('identifier', help="File ID or filename")

    # files reindex
    reindex_parser = files_sub.add_parser('reindex', help="Reindex session file(s)")
    reindex_parser.add_argument('identifier', nargs='?', help="File ID, filename, or --all")
    reindex_parser.add_argument('--all', action='store_true', help="Reindex all files")

    # files check
    check_parser = files_sub.add_parser('check', help="Check consistency between DB and filesystem")
    check_parser.add_argument('--fix', action='store_true', help="Automatically fix issues")

    # ========================================================================
    # SEARCH Commands
    # ========================================================================
    search_parser = subparsers.add_parser('search', help="Search HTTP requests")
    search_parser.add_argument('--method', help="Filter by HTTP method")
    search_parser.add_argument('--url', help="Filter by URL (contains)")
    search_parser.add_argument('--host', help="Filter by host (contains)")
    search_parser.add_argument('--path', help="Filter by path (contains)")
    search_parser.add_argument('--status', type=int, help="Filter by status code")
    search_parser.add_argument('--status-min', type=int, help="Minimum status code")
    search_parser.add_argument('--status-max', type=int, help="Maximum status code")
    search_parser.add_argument('--header', action='append', help="Header filter (format: key:value or :value or key:)")
    search_parser.add_argument('--cookie', action='append', help="Cookie filter (format: key:value or :value or key:)")
    search_parser.add_argument('--body', help="Search in body content")
    search_parser.add_argument('--limit', type=int, default=100, help="Max results (default: 100)")
    search_parser.add_argument('--offset', type=int, default=0, help="Skip N results")
    search_parser.add_argument('--format', choices=['table', 'json'], default='table', help="Output format")
    search_parser.add_argument('--sort', choices=['time', 'duration', 'status'], default='time', help="Sort by")
    search_parser.add_argument('--asc', action='store_true', help="Sort ascending (default: descending)")

    # ========================================================================
    # STATS Command
    # ========================================================================
    stats_parser = subparsers.add_parser('stats', help="Show statistics")
    stats_parser.add_argument('--file', help="Filter by file ID or filename")
    stats_parser.add_argument('--format', choices=['table', 'json'], default='table', help="Output format")

    # ========================================================================
    # KEYS Command
    # ========================================================================
    keys_parser = subparsers.add_parser('keys', help="List available header/cookie keys")
    keys_parser.add_argument('type', choices=['header', 'cookie'], help="Key type")
    keys_parser.add_argument('--prefix', help="Filter by prefix (autocomplete)")
    keys_parser.add_argument('--limit', type=int, default=50, help="Max results")

    # ========================================================================
    # CONFIG Commands
    # ========================================================================
    config_parser = subparsers.add_parser('config', help="Configuration management")
    config_sub = config_parser.add_subparsers(dest='config_command', required=True)

    get_parser = config_sub.add_parser('get', help="Get configuration value")
    get_parser.add_argument('key', nargs='?', help="Config key (omit to show all)")

    set_parser = config_sub.add_parser('set', help="Set configuration value")
    set_parser.add_argument('key', help="Config key")
    set_parser.add_argument('value', help="Config value")

    # ========================================================================
    # Parse and Route
    # ========================================================================
    args = parser.parse_args()

    # Initialize manager
    manager = Manager(args.data_dir)

    # Route to appropriate handler
    try:
        if args.command == 'init':
            manager.cmd_init(args)

        elif args.command == 'server':
            # TODO: Implement in Milestone 6
            print("Server command not implemented yet (Milestone 6)")
            print("This will start the MCP server in stdio mode")
            sys.exit(1)

        elif args.command == 'db':
            if args.db_command == 'status':
                manager.cmd_db_status(args)
            elif args.db_command == 'vacuum':
                manager.cmd_db_vacuum(args)
            elif args.db_command == 'stats':
                manager.cmd_db_stats(args)
            elif args.db_command == 'reset':
                manager.cmd_db_reset(args)
            elif args.db_command == 'migrate':
                manager.cmd_db_migrate(args)

        elif args.command == 'files':
            if args.files_command == 'list':
                manager.cmd_files_list(args)
            elif args.files_command == 'add':
                manager.cmd_files_add(args)
            elif args.files_command == 'add-dir':
                manager.cmd_files_add_dir(args)
            elif args.files_command == 'remove':
                manager.cmd_files_remove(args)
            elif args.files_command == 'info':
                manager.cmd_files_info(args)
            elif args.files_command == 'reindex':
                manager.cmd_files_reindex(args)
            elif args.files_command == 'check':
                manager.cmd_files_check(args)

        elif args.command == 'search':
            manager.cmd_search(args)

        elif args.command == 'stats':
            manager.cmd_stats(args)

        elif args.command == 'keys':
            manager.cmd_keys(args)

        elif args.command == 'config':
            if args.config_command == 'get':
                manager.cmd_config_get(args)
            elif args.config_command == 'set':
                manager.cmd_config_set(args)

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if '--debug' in sys.argv:
            raise
        sys.exit(1)


if __name__ == "__main__":
    main()
