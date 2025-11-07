"""Async file tracking and change detection for session files."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

import aiofiles

from .database import Database


async def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file for change detection.

    Args:
        file_path: Path to file

    Returns:
        Hex-encoded SHA256 hash

    Note:
        Reads file in chunks for memory efficiency.
    """
    sha256 = hashlib.sha256()
    async with aiofiles.open(file_path, 'rb') as f:
        while chunk := await f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


class FileTracker:
    """Track session files in database (async version)."""

    def __init__(self, db: Database):
        """Initialize file tracker.

        Args:
            db: Database instance
        """
        self.db = db

    async def add_file(self, file_path: Path) -> int:
        """Add or update file in tracking table.

        Args:
            file_path: Path to session file

        Returns:
            File ID from database

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Compute file metadata
        file_hash = await compute_file_hash(file_path)
        file_size = file_path.stat().st_size
        last_modified = int(file_path.stat().st_mtime)
        indexed_at = int(time.time())

        # Check if file already exists
        conn = await self.db.connect()
        cursor = await conn.execute(
            "SELECT id, file_hash FROM session_files WHERE file_path = ?",
            (str(file_path),)
        )
        row = await cursor.fetchone()

        if row:
            file_id = row[0]
            existing_hash = row[1]

            # Update if hash changed
            if existing_hash != file_hash:
                await conn.execute(
                    """
                    UPDATE session_files
                    SET file_hash = ?, file_size = ?, last_modified = ?,
                        indexed_at = ?, status = 'active'
                    WHERE id = ?
                    """,
                    (file_hash, file_size, last_modified, indexed_at, file_id)
                )
                await conn.commit()
        else:
            # Insert new file
            cursor = await conn.execute(
                """
                INSERT INTO session_files
                (file_path, file_hash, file_size, last_modified, indexed_at, status)
                VALUES (?, ?, ?, ?, ?, 'active')
                """,
                (str(file_path), file_hash, file_size, last_modified, indexed_at)
            )
            file_id = cursor.lastrowid
            await conn.commit()

        return file_id

    async def find_duplicate_by_hash(self, file_hash: str) -> Optional[dict]:
        """Check if a file with the same hash already exists.

        Args:
            file_hash: SHA256 hash of file

        Returns:
            File info dict if duplicate found, None otherwise
        """
        conn = await self.db.connect()
        cursor = await conn.execute(
            """
            SELECT id, file_path, file_size, indexed_at
            FROM session_files
            WHERE file_hash = ?
            """,
            (file_hash,)
        )
        row = await cursor.fetchone()

        if row:
            return {
                'id': row[0],
                'file_path': row[1],
                'file_size': row[2],
                'indexed_at': row[3],
                'file_hash': file_hash
            }
        return None

    async def remove_file(self, file_id: int) -> int:
        """Remove file from tracking (cascade deletes all requests).

        Args:
            file_id: File ID from database

        Returns:
            Number of requests deleted
        """
        conn = await self.db.connect()

        # Count requests before deleting
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM requests WHERE file_id = ?",
            (file_id,)
        )
        row = await cursor.fetchone()
        request_count = row[0]

        # Delete file (cascade will handle requests)
        await conn.execute(
            "DELETE FROM session_files WHERE id = ?",
            (file_id,)
        )
        await conn.commit()

        return request_count

    async def get_file_info(self, file_id: int) -> Optional[dict]:
        """Get file information.

        Args:
            file_id: File ID from database

        Returns:
            Dict with file information or None if not found
        """
        conn = await self.db.connect()
        cursor = await conn.execute(
            """
            SELECT
                sf.id, sf.file_path, sf.file_hash, sf.file_size,
                sf.last_modified, sf.indexed_at, sf.status,
                COUNT(r.id) as request_count
            FROM session_files sf
            LEFT JOIN requests r ON r.file_id = sf.id
            WHERE sf.id = ?
            GROUP BY sf.id
            """,
            (file_id,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return {
            'id': row[0],
            'file_path': row[1],
            'filename': Path(row[1]).name,
            'file_hash': row[2],
            'file_size': row[3],
            'last_modified': row[4],
            'indexed_at': row[5],
            'status': row[6],
            'request_count': row[7],
        }

    async def list_files(self) -> list[dict]:
        """List all tracked files.

        Returns:
            List of file information dicts
        """
        conn = await self.db.connect()
        cursor = await conn.execute(
            """
            SELECT
                sf.id, sf.file_path, sf.file_hash, sf.file_size,
                sf.last_modified, sf.indexed_at, sf.status,
                COUNT(r.id) as request_count
            FROM session_files sf
            LEFT JOIN requests r ON r.file_id = sf.id
            WHERE sf.status = 'active'
            GROUP BY sf.id
            ORDER BY sf.indexed_at DESC
            """
        )

        files = []
        async for row in cursor:
            files.append({
                'id': row[0],
                'file_path': row[1],
                'filename': Path(row[1]).name,
                'file_hash': row[2],
                'file_size': row[3],
                'last_modified': row[4],
                'indexed_at': row[5],
                'status': row[6],
                'request_count': row[7],
            })

        return files

    async def resolve_file_id(self, identifier: str | int) -> Optional[int]:
        """Resolve file identifier to file ID.

        Args:
            identifier: File ID (int) or filename (str)

        Returns:
            File ID or None if not found
        """
        conn = await self.db.connect()

        # Try as integer ID first
        try:
            file_id = int(identifier)
            cursor = await conn.execute(
                "SELECT id FROM session_files WHERE id = ?",
                (file_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else None
        except ValueError:
            pass

        # Try as filename
        cursor = await conn.execute(
            "SELECT id FROM session_files WHERE file_path LIKE ?",
            (f"%{identifier}",)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def file_needs_reindex(self, file_path: Path) -> bool:
        """Check if file needs reindexing based on hash.

        Args:
            file_path: Path to session file

        Returns:
            True if file has changed or is not tracked
        """
        if not file_path.exists():
            return False

        conn = await self.db.connect()
        cursor = await conn.execute(
            "SELECT file_hash FROM session_files WHERE file_path = ?",
            (str(file_path),)
        )
        row = await cursor.fetchone()

        if not row:
            return True  # Not tracked

        stored_hash = row[0]
        current_hash = await compute_file_hash(file_path)

        return stored_hash != current_hash

    def scan_directory(
        self,
        directory: Path,
        pattern: str = "????_??_??__??_??_??*",
        recursive: bool = True
    ) -> list[Path]:
        """Scan directory for HTTP Catcher session files.

        HTTP Catcher session files follow the naming pattern:
        YYYY_MM_DD__HH_MM_SS (e.g., 2024_11_07__16_23_04)

        After copying to sessions directory, they may have .session extension.

        Args:
            directory: Directory to scan
            pattern: Glob pattern (default: ????_??_??__??_??_??*)
            recursive: If True, scan recursively

        Returns:
            List of file paths matching pattern

        Note:
            This method is synchronous as pathlib's glob operations are already
            efficient and non-blocking for directory scanning.
        """
        if recursive:
            return list(directory.rglob(pattern))
        else:
            return list(directory.glob(pattern))

    async def check_consistency(self) -> dict[str, list]:
        """Check consistency between database and filesystem.

        Returns:
            Dict with 'orphaned', 'untracked', 'modified' file lists
        """
        issues = {
            'orphaned': [],
            'untracked': [],
            'modified': []
        }

        conn = await self.db.connect()

        # Check orphaned (in DB but file missing)
        cursor = await conn.execute(
            "SELECT id, file_path FROM session_files WHERE status = 'active'"
        )
        rows = await cursor.fetchall()
        for row in rows:
            file_id, file_path = row[0], row[1]
            if not Path(file_path).exists():
                file_info = await self.get_file_info(file_id)
                if file_info:
                    issues['orphaned'].append(file_info)

        # Check modified (hash mismatch)
        cursor = await conn.execute(
            "SELECT id, file_path, file_hash FROM session_files WHERE status = 'active'"
        )
        rows = await cursor.fetchall()
        for row in rows:
            file_id, file_path, db_hash = row[0], row[1], row[2]
            path = Path(file_path)
            if path.exists():
                current_hash = await compute_file_hash(path)
                if current_hash != db_hash:
                    issues['modified'].append({
                        'id': file_id,
                        'filename': path.name,
                        'file_path': file_path,
                        'db_hash': db_hash,
                        'file_hash': current_hash
                    })

        return issues

    async def find_untracked_files(self, base_paths: list[Path], pattern: str = "????_??_??__??_??_??*") -> list[Path]:
        """Find files in base_paths that are not in database.

        Args:
            base_paths: Directories to scan
            pattern: Glob pattern (default: ????_??_??__??_??_??*)

        Returns:
            List of untracked file paths
        """
        conn = await self.db.connect()

        # Get all tracked file paths
        cursor = await conn.execute(
            "SELECT file_path FROM session_files WHERE status = 'active'"
        )
        tracked = set(row[0] async for row in cursor)

        # Scan directories
        untracked = []
        for base_path in base_paths:
            if not base_path.exists():
                continue
            for file_path in self.scan_directory(base_path, pattern, recursive=True):
                if str(file_path) not in tracked:
                    untracked.append(file_path)

        return untracked
