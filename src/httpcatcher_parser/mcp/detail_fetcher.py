"""Async request detail fetcher with lazy body loading."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional

import aiofiles

from .database import Database


class DetailLevel(Enum):
    """Level of detail to fetch."""

    FULL = "full"  # All data including headers, cookies, bodies
    HEADERS_ONLY = "headers_only"  # Request/response lines and headers
    REQUEST_ONLY = "request_only"  # Only request data
    RESPONSE_ONLY = "response_only"  # Only response data
    METADATA = "metadata"  # Only method, URL, status, timestamps


class DetailFetcher:
    """Fetch request details with lazy body loading (async version)."""

    def __init__(self, db: Database):
        """Initialize detail fetcher.

        Args:
            db: Database instance
        """
        self.db = db

    async def get_request_details(
        self,
        request_id: int,
        detail_level: DetailLevel = DetailLevel.FULL
    ) -> Optional[dict]:
        """Get request details with optional body loading.

        Args:
            request_id: Request ID
            detail_level: Level of detail to fetch

        Returns:
            Dict with request details or None if not found
        """
        # Fetch basic request data
        conn = await self.db.connect()
        cursor = await conn.execute(
            """
            SELECT
                r.id, r.file_id, r.method, r.url, r.host, r.path, r.status_code,
                r.req_timestamp, r.resp_timestamp, r.duration_ms,
                r.connection_id, r.port,
                r.req_content_type, r.resp_content_type, r.resp_content_category,
                r.req_headers_json, r.resp_headers_json,
                r.req_cookies_json, r.resp_cookies_json,
                r.req_body_size, r.resp_body_size,
                r.req_body_offset, r.resp_body_offset, r.req_body_length, r.resp_body_length,
                r.req_body_blob, r.resp_body_blob,
                r.req_body_preview, r.resp_body_preview,
                sf.file_path
            FROM requests r
            JOIN session_files sf ON sf.id = r.file_id
            WHERE r.id = ?
            """,
            (request_id,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        # Build response based on detail level
        details = {}

        # Metadata (always included)
        details['id'] = row[0]
        details['file_id'] = row[1]
        details['method'] = row[2]
        details['url'] = row[3]
        details['host'] = row[4]
        details['path'] = row[5]
        details['status_code'] = row[6]
        details['req_timestamp'] = row[7]
        details['resp_timestamp'] = row[8]
        details['duration_ms'] = row[9]

        if detail_level == DetailLevel.METADATA:
            return details

        # Connection info
        details['connection_id'] = row[10]
        details['port'] = row[11]

        # Content types
        details['req_content_type'] = row[12]
        details['resp_content_type'] = row[13]
        details['resp_content_category'] = row[14]

        # Headers and cookies (if not response-only)
        if detail_level != DetailLevel.RESPONSE_ONLY:
            details['request'] = {
                'headers': json.loads(row[15]) if row[15] else [],
                'cookies': json.loads(row[17]) if row[17] else [],
                'body_size': row[19],
                'body_preview': row[27]
            }

        # Response headers and cookies (if not request-only)
        if detail_level != DetailLevel.REQUEST_ONLY:
            details['response'] = {
                'headers': json.loads(row[16]) if row[16] else [],
                'cookies': json.loads(row[18]) if row[18] else [],
                'body_size': row[20],
                'body_preview': row[28]
            }

        # Load full bodies from BLOBs (preferred) or from file (fallback)
        file_path = Path(row[29])

        if detail_level in (DetailLevel.FULL, DetailLevel.REQUEST_ONLY):
            body_data = None

            # Try loading from BLOB first (preferred method)
            if row[25] is not None:  # req_body_blob
                body_data = row[25]
            # Fallback to file-based loading if offsets are available
            elif row[21] is not None and row[23] is not None:  # req_body_offset, req_body_length
                body_data = await self._load_body_from_file(file_path, row[21], row[23])

            # Bodies are already decompressed during indexing
            if body_data is not None:
                details['request']['body'] = body_data

        if detail_level in (DetailLevel.FULL, DetailLevel.RESPONSE_ONLY):
            body_data = None

            # Try loading from BLOB first (preferred method)
            if row[26] is not None:  # resp_body_blob
                body_data = row[26]
            # Fallback to file-based loading if offsets are available
            elif row[22] is not None and row[24] is not None:  # resp_body_offset, resp_body_length
                body_data = await self._load_body_from_file(file_path, row[22], row[24])

            # Bodies are already decompressed during indexing
            if body_data is not None:
                details['response']['body'] = body_data

        return details

    async def _load_body_from_file(
        self,
        file_path: Path,
        offset: int,
        length: int
    ) -> Optional[bytes]:
        """Load body data from file using async file operations.

        Args:
            file_path: Path to session file
            offset: Byte offset in file
            length: Number of bytes to read

        Returns:
            Body bytes or None if error
        """
        if not file_path.exists():
            return None

        try:
            async with aiofiles.open(file_path, 'rb') as f:
                await f.seek(offset)
                return await f.read(length)
        except Exception:
            return None

    async def get_request_body(
        self,
        request_id: int,
        request: bool = True,
        encoding: str = 'utf-8'
    ) -> Optional[str | bytes]:
        """Get just the body of a request or response.

        Args:
            request_id: Request ID
            request: If True, return request body; else response body
            encoding: Text encoding for decoding (None to return bytes)

        Returns:
            Body as string or bytes, or None if not found
        """
        offset_col = "req_body_offset" if request else "resp_body_offset"
        length_col = "req_body_length" if request else "resp_body_length"
        preview_col = "req_body_preview" if request else "resp_body_preview"
        blob_col = "req_body_blob" if request else "resp_body_blob"

        conn = await self.db.connect()
        cursor = await conn.execute(
            f"""
            SELECT
                sf.file_path, r.{offset_col}, r.{length_col},
                r.{preview_col}, r.{blob_col}
            FROM requests r
            JOIN session_files sf ON sf.id = r.file_id
            WHERE r.id = ?
            """,
            (request_id,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        file_path = Path(row[0])
        offset = row[1]
        length = row[2]
        preview = row[3]
        blob_data = row[4]

        # Try loading from BLOB first (preferred method, already decompressed)
        if blob_data is not None:
            body_data = blob_data
        # Fallback to file-based loading if offsets are available
        elif offset is not None and length is not None:
            body_data = await self._load_body_from_file(file_path, offset, length)
        else:
            # No body available, return preview
            return preview

        if not body_data:
            return preview

        # Bodies are already decompressed during indexing
        # Decode if encoding specified
        if encoding:
            try:
                return body_data.decode(encoding, errors='replace')
            except Exception:
                return body_data

        return body_data

    @staticmethod
    def format_headers(headers: list[dict]) -> str:
        """Format headers for display.

        Args:
            headers: List of header dicts with 'name' and 'value'

        Returns:
            Formatted string
        """
        if not headers:
            return "(no headers)"

        lines = []
        for h in headers:
            name = h.get('name', 'Unknown')
            value = h.get('value', '')
            lines.append(f"  {name}: {value}")

        return "\n".join(lines)

    @staticmethod
    def format_cookies(cookies: list[dict]) -> str:
        """Format cookies for display.

        Args:
            cookies: List of cookie dicts

        Returns:
            Formatted string
        """
        if not cookies:
            return "(no cookies)"

        lines = []
        for c in cookies:
            name = c.get('name', 'Unknown')
            value = c.get('value', '')
            # Truncate long values
            if len(value) > 50:
                value = value[:47] + "..."
            lines.append(f"  {name} = {value}")

            # Show attributes for response cookies
            if 'domain' in c and c['domain']:
                lines.append(f"    Domain: {c['domain']}")
            if 'path' in c and c['path']:
                lines.append(f"    Path: {c['path']}")
            if c.get('http_only'):
                lines.append("    HttpOnly")
            if c.get('secure'):
                lines.append("    Secure")
            if c.get('same_site'):
                lines.append(f"    SameSite: {c['same_site']}")

        return "\n".join(lines)
