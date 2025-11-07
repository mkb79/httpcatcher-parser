"""File tracking and change detection for session files."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

from .database import Database


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file for change detection.

    Args:
        file_path: Path to file

    Returns:
        Hex-encoded SHA256 hash

    Note:
        Reads file in chunks for memory efficiency.
    """
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


class FileTracker:
    """Track session files in database."""

    def __init__(self, db: Database):
        """Initialize file tracker.

        Args:
            db: Database instance
        """
        self.db = db

    def add_file(self, file_path: Path) -> int:
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
        file_hash = compute_file_hash(file_path)
        file_size = file_path.stat().st_size
        last_modified = int(file_path.stat().st_mtime)
        indexed_at = int(time.time())

        # Check if file already exists
        cursor = self.db.conn.execute(
            "SELECT id, file_hash FROM session_files WHERE file_path = ?",
            (str(file_path),)
        )
        row = cursor.fetchone()

        if row:
            file_id = row[0]
            existing_hash = row[1]

            # Update if hash changed
            if existing_hash != file_hash:
                self.db.conn.execute(
                    """
                    UPDATE session_files
                    SET file_hash = ?, file_size = ?, last_modified = ?,
                        indexed_at = ?, status = 'active'
                    WHERE id = ?
                    """,
                    (file_hash, file_size, last_modified, indexed_at, file_id)
                )
                self.db.conn.commit()
        else:
            # Insert new file
            cursor = self.db.conn.execute(
                """
                INSERT INTO session_files
                (file_path, file_hash, file_size, last_modified, indexed_at, status)
                VALUES (?, ?, ?, ?, ?, 'active')
                """,
                (str(file_path), file_hash, file_size, last_modified, indexed_at)
            )
            file_id = cursor.lastrowid
            self.db.conn.commit()

        return file_id

    def remove_file(self, file_id: int) -> int:
        """Remove file from tracking (cascade deletes all requests).

        Args:
            file_id: File ID from database

        Returns:
            Number of requests deleted
        """
        # Count requests before deleting
        cursor = self.db.conn.execute(
            "SELECT COUNT(*) FROM requests WHERE file_id = ?",
            (file_id,)
        )
        request_count = cursor.fetchone()[0]

        # Delete file (cascade will handle requests)
        self.db.conn.execute(
            "DELETE FROM session_files WHERE id = ?",
            (file_id,)
        )
        self.db.conn.commit()

        return request_count

    def get_file_info(self, file_id: int) -> Optional[dict]:
        """Get file information.

        Args:
            file_id: File ID from database

        Returns:
            Dict with file information or None if not found
        """
        cursor = self.db.conn.execute(
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
        row = cursor.fetchone()

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

    def list_files(self) -> list[dict]:
        """List all tracked files.

        Returns:
            List of file information dicts
        """
        cursor = self.db.conn.execute(
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
        for row in cursor.fetchall():
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

    def resolve_file_id(self, identifier: str | int) -> Optional[int]:
        """Resolve file identifier to file ID.

        Args:
            identifier: File ID (int) or filename (str)

        Returns:
            File ID or None if not found
        """
        # Try as integer ID first
        try:
            file_id = int(identifier)
            cursor = self.db.conn.execute(
                "SELECT id FROM session_files WHERE id = ?",
                (file_id,)
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except ValueError:
            pass

        # Try as filename
        cursor = self.db.conn.execute(
            "SELECT id FROM session_files WHERE file_path LIKE ?",
            (f"%{identifier}",)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def file_needs_reindex(self, file_path: Path) -> bool:
        """Check if file needs reindexing based on hash.

        Args:
            file_path: Path to session file

        Returns:
            True if file has changed or is not tracked
        """
        if not file_path.exists():
            return False

        cursor = self.db.conn.execute(
            "SELECT file_hash FROM session_files WHERE file_path = ?",
            (str(file_path),)
        )
        row = cursor.fetchone()

        if not row:
            return True  # Not tracked

        stored_hash = row[0]
        current_hash = compute_file_hash(file_path)

        return stored_hash != current_hash
