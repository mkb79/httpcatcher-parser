"""Async session file indexer - parses files and populates database."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..hc_parser import (
    ConnectionFrame,
    HttpCatcherScanner,
    RequestBody,
    RequestHeader,
    RequestMainInfo,
    ResponseBody,
    ResponseHeader,
)
from .content_analyzer import categorize_content_type, parse_content_type
from .database import Database


@dataclass
class RequestAggregation:
    """Aggregates all data for a single request."""

    request_id: int
    connection_id: Optional[int] = None

    # Request data
    req_header_ts: Optional[int] = None
    req_header_payload: Optional[bytes] = None
    req_bodies: list[bytes] = field(default_factory=list)

    # Response data
    resp_header_ts: Optional[int] = None
    resp_header_payload: Optional[bytes] = None
    resp_bodies: list[bytes] = field(default_factory=list)
    resp_last_ts: Optional[int] = None

    # RMI timestamp
    rmi_ts_leading: Optional[int] = None

    # Derived fields
    method: Optional[str] = None
    url: Optional[str] = None
    host: Optional[str] = None
    path: Optional[str] = None
    status_code: Optional[int] = None


class KeyIndexer:
    """Manages normalized key lookup with caching (async version)."""

    def __init__(self, db: Database, prefetch: bool = True):
        self.db = db
        self._header_cache: dict[str, int] = {}
        self._cookie_cache: dict[str, int] = {}
        self._prefetch_done = False
        self._should_prefetch = prefetch

    async def ensure_prefetch(self):
        """Ensure prefetch is done (call once at start)."""
        if self._should_prefetch and not self._prefetch_done:
            await self._prefetch_keys()
            self._prefetch_done = True

    async def get_or_create_header_key(self, name: str) -> int:
        """Get or create header key ID (race-condition safe)."""
        if name in self._header_cache:
            return self._header_cache[name]

        conn = await self.db.connect()

        # Use INSERT OR IGNORE to handle race conditions in parallel processing
        await conn.execute(
            "INSERT OR IGNORE INTO header_keys (name, name_lower, usage_count) VALUES (?, ?, 0)",
            (name, name.lower())
        )

        # Always SELECT to get the ID (whether we inserted it or it already existed)
        cursor = await conn.execute(
            "SELECT id FROM header_keys WHERE name = ?",
            (name,)
        )
        row = await cursor.fetchone()
        key_id = row[0]

        self._header_cache[name] = key_id
        return key_id

    async def get_or_create_cookie_key(self, name: str) -> int:
        """Get or create cookie key ID (race-condition safe)."""
        if name in self._cookie_cache:
            return self._cookie_cache[name]

        conn = await self.db.connect()

        # Use INSERT OR IGNORE to handle race conditions in parallel processing
        await conn.execute(
            "INSERT OR IGNORE INTO cookie_keys (name, name_lower, usage_count) VALUES (?, ?, 0)",
            (name, name.lower())
        )

        # Always SELECT to get the ID (whether we inserted it or it already existed)
        cursor = await conn.execute(
            "SELECT id FROM cookie_keys WHERE name = ?",
            (name,)
        )
        row = await cursor.fetchone()
        key_id = row[0]

        self._cookie_cache[name] = key_id
        return key_id

    async def _prefetch_keys(self):
        """Prefetch all existing keys into cache for faster lookups."""
        conn = await self.db.connect()

        # Prefetch header keys
        cursor = await conn.execute("SELECT name, id FROM header_keys")
        async for row in cursor:
            name, key_id = row
            self._header_cache[name] = key_id

        # Prefetch cookie keys
        cursor = await conn.execute("SELECT name, id FROM cookie_keys")
        async for row in cursor:
            name, key_id = row
            self._cookie_cache[name] = key_id


def _parse_headers(payload: Optional[bytes]) -> tuple[Optional[str], list[tuple[str, str]]]:
    """Parse HTTP header blob into start line and header pairs."""
    if not payload:
        return None, []

    try:
        head, _rest = payload.split(b"\r\n\r\n", 1)
    except ValueError:
        head = payload

    try:
        text = head.decode("latin-1", "replace")
    except Exception:
        return None, []

    lines = text.split("\r\n")
    first = lines[0] if lines else None

    pairs: list[tuple[str, str]] = []
    for ln in lines[1:]:
        if not ln or ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        pairs.append((k.strip(), v.strip()))

    return first, pairs


def _parse_cookies_from_headers(headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Extract cookies from Cookie headers."""
    cookies: list[tuple[str, str]] = []
    for k, v in headers:
        if k.lower() == "cookie":
            parts = [p.strip() for p in v.split(";") if p.strip()]
            for part in parts:
                if "=" in part:
                    name, val = part.split("=", 1)
                    cookies.append((name.strip(), val.strip()))
    return cookies


def _parse_set_cookies(headers: list[tuple[str, str]]) -> list[dict]:
    """Parse Set-Cookie headers into cookie dicts."""
    cookies: list[dict] = []
    for k, v in headers:
        if k.lower() != "set-cookie":
            continue

        parts = [p.strip() for p in v.split(";")]
        if not parts or "=" not in parts[0]:
            continue

        cname, cval = parts[0].split("=", 1)
        cookie = {
            'name': cname.strip(),
            'value': cval.strip(),
            'domain': None,
            'path': None,
            'expires': None,
            'http_only': False,
            'secure': False,
            'same_site': None,
        }

        for attr in parts[1:]:
            if not attr:
                continue
            kv = attr.split("=", 1)
            aname = kv[0].strip().lower()
            aval = kv[1].strip() if len(kv) == 2 else None

            if aname == "path" and aval:
                cookie['path'] = aval
            elif aname == "domain" and aval:
                cookie['domain'] = aval
            elif aname == "expires" and aval:
                cookie['expires'] = aval
            elif aname == "samesite" and aval:
                cookie['same_site'] = aval
            elif aname == "httponly":
                cookie['http_only'] = True
            elif aname == "secure":
                cookie['secure'] = True

        cookies.append(cookie)

    return cookies


def _parse_request_line(line: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Parse request start line."""
    if not line:
        return None, None
    parts = line.split(" ")
    if len(parts) >= 2:
        return parts[0], parts[1]  # method, url
    return None, None


def _parse_status_line(line: Optional[str]) -> Optional[int]:
    """Parse response status line."""
    if not line or not line.startswith("HTTP/"):
        return None
    parts = line.split(" ", 2)
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return None


class Indexer:
    """Indexes session files into database (async version)."""

    def __init__(self, db: Database, scanner: HttpCatcherScanner):
        self.db = db
        self.scanner = scanner

    async def index_file(self, file_path: Path, force_reindex: bool = False) -> dict:
        """Index a session file.

        Args:
            file_path: Path to session file
            force_reindex: If True, delete old entries and re-index

        Returns:
            Dict with indexing results
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Import FileTracker here to avoid circular import
        from .file_tracker import FileTracker
        file_tracker = FileTracker(self.db)

        # Check if reindex needed
        if not force_reindex and not await file_tracker.file_needs_reindex(file_path):
            file_id = await file_tracker.resolve_file_id(str(file_path))
            return {
                'indexed': False,
                'requests_added': 0,
                'file_id': file_id,
                'reason': 'unchanged'
            }

        # Add/update file in tracker
        file_id = await file_tracker.add_file(file_path)

        # Delete old requests if exists
        conn = await self.db.connect()
        await conn.execute("DELETE FROM requests WHERE file_id = ?", (file_id,))

        # Parse and aggregate requests
        aggregations = self._parse_file(file_path)

        # Batch insert into database
        requests_added = await self._bulk_insert(file_id, aggregations)

        return {
            'indexed': True,
            'requests_added': requests_added,
            'file_id': file_id
        }

    def _parse_file(self, file_path: Path) -> dict[int, RequestAggregation]:
        """Parse file and aggregate by request_id."""
        aggregations: dict[int, RequestAggregation] = {}
        connections: dict[int, ConnectionFrame] = {}

        for item in self.scanner.scan(str(file_path)):
            if isinstance(item, ConnectionFrame):
                connections[item.connection_id] = item

            elif isinstance(item, RequestMainInfo):
                agg = aggregations.setdefault(item.request_id, RequestAggregation(item.request_id))
                agg.connection_id = item.connection_id
                agg.rmi_ts_leading = item.ts_leading

            elif isinstance(item, RequestHeader):
                agg = aggregations.setdefault(item.request_id, RequestAggregation(item.request_id))
                agg.req_header_ts = item.ts_post
                agg.req_header_payload = item.payload

                # Parse request line
                first, _ = _parse_headers(item.payload)
                method, url = _parse_request_line(first)
                agg.method = method
                agg.url = url

            elif isinstance(item, RequestBody):
                agg = aggregations.setdefault(item.request_id, RequestAggregation(item.request_id))
                if item.payload:
                    agg.req_bodies.append(item.payload)

            elif isinstance(item, ResponseHeader):
                agg = aggregations.setdefault(item.request_id, RequestAggregation(item.request_id))
                agg.resp_header_ts = item.ts_post
                agg.resp_header_payload = item.payload

                # Parse status line
                first, _ = _parse_headers(item.payload)
                agg.status_code = _parse_status_line(first)

            elif isinstance(item, ResponseBody):
                agg = aggregations.setdefault(item.request_id, RequestAggregation(item.request_id))
                if item.payload:
                    agg.resp_bodies.append(item.payload)
                agg.resp_last_ts = item.ts_post

        # Enrich with connection data
        for agg in aggregations.values():
            if agg.connection_id and agg.connection_id in connections:
                conn = connections[agg.connection_id]
                agg.host = conn.host
                if agg.url and agg.url.startswith('/'):
                    agg.path = agg.url
                    agg.url = f"https://{conn.host}{agg.url}"

        return aggregations

    async def _bulk_insert(self, file_id: int, aggregations: dict[int, RequestAggregation]) -> int:
        """Bulk insert aggregated data into database."""
        if not aggregations:
            return 0

        key_indexer = KeyIndexer(self.db)
        await key_indexer.ensure_prefetch()

        # Prepare batch data for requests
        requests_batch = []
        # We'll collect headers/cookies data with original_request_id as key
        headers_data = {}  # original_request_id -> (req_headers, resp_headers)
        cookies_data = {}  # original_request_id -> (req_cookies, resp_cookies_list)

        for req_id, agg in aggregations.items():
            # Parse headers
            _, req_headers = _parse_headers(agg.req_header_payload)
            _, resp_headers = _parse_headers(agg.resp_header_payload)

            # Get content types
            req_ct = next((v for k, v in req_headers if k.lower() == 'content-type'), None)
            resp_ct = next((v for k, v in resp_headers if k.lower() == 'content-type'), None)
            resp_ct_category = categorize_content_type(resp_ct)

            # Serialize headers/cookies to JSON for quick detail retrieval
            req_headers_json = json.dumps([{'name': k, 'value': v} for k, v in req_headers])
            resp_headers_json = json.dumps([{'name': k, 'value': v} for k, v in resp_headers])

            req_cookies = _parse_cookies_from_headers(req_headers)
            resp_cookies_list = _parse_set_cookies(resp_headers)
            req_cookies_json = json.dumps([{'name': n, 'value': v} for n, v in req_cookies])
            resp_cookies_json = json.dumps(resp_cookies_list)

            # Body sizes and previews
            req_body = b''.join(agg.req_bodies)
            resp_body = b''.join(agg.resp_bodies)
            req_body_preview = req_body[:500].decode('utf-8', 'replace') if req_body else None
            resp_body_preview = resp_body[:500].decode('utf-8', 'replace') if resp_body else None

            # Calculate duration
            duration_ms = None
            if agg.req_header_ts and agg.resp_header_ts:
                duration_ms = agg.resp_header_ts - agg.req_header_ts

            # Insert request (without id - will be auto-generated)
            requests_batch.append((
                file_id, req_id,  # file_id, original_request_id
                agg.method, agg.url, agg.host, agg.path, agg.status_code,
                agg.req_header_ts, agg.resp_header_ts, duration_ms,
                agg.connection_id, None,  # port TODO: get from connection
                req_ct, resp_ct, resp_ct_category,
                req_headers_json, resp_headers_json,
                req_cookies_json, resp_cookies_json,
                len(req_body), len(resp_body),
                None, None, None, None,  # body offsets TODO: implement lazy loading
                req_body_preview, resp_body_preview
            ))

            # Store headers/cookies data for later insertion with correct db IDs
            headers_data[req_id] = (req_headers, resp_headers)
            cookies_data[req_id] = (req_cookies, resp_cookies_list)

        # Insert requests - id will be auto-generated
        conn = await self.db.connect()
        await conn.executemany(
            """
            INSERT INTO requests (
                file_id, original_request_id, method, url, host, path, status_code,
                req_timestamp, resp_timestamp, duration_ms,
                connection_id, port,
                req_content_type, resp_content_type, resp_content_category,
                req_headers_json, resp_headers_json,
                req_cookies_json, resp_cookies_json,
                req_body_size, resp_body_size,
                req_body_offset, resp_body_offset, req_body_length, resp_body_length,
                req_body_preview, resp_body_preview
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            requests_batch
        )

        # Get the mapping of original_request_id -> new db id
        cursor = await conn.execute(
            "SELECT id, original_request_id FROM requests WHERE file_id = ?",
            (file_id,)
        )
        id_mapping = {orig_id: db_id async for db_id, orig_id in cursor}

        # Now prepare headers/cookies batches with correct db IDs
        req_headers_batch = []
        resp_headers_batch = []
        req_cookies_batch = []
        resp_cookies_batch = []

        for orig_req_id, (req_headers, resp_headers) in headers_data.items():
            db_id = id_mapping.get(orig_req_id)
            if not db_id:
                continue

            # Insert headers with normalized keys
            for name, value in req_headers:
                key_id = await key_indexer.get_or_create_header_key(name)
                req_headers_batch.append((db_id, key_id, value))

            for name, value in resp_headers:
                key_id = await key_indexer.get_or_create_header_key(name)
                resp_headers_batch.append((db_id, key_id, value))

        for orig_req_id, (req_cookies, resp_cookies_list) in cookies_data.items():
            db_id = id_mapping.get(orig_req_id)
            if not db_id:
                continue

            # Insert cookies with normalized keys
            for name, value in req_cookies:
                key_id = await key_indexer.get_or_create_cookie_key(name)
                req_cookies_batch.append((db_id, key_id, value))

            for cookie in resp_cookies_list:
                key_id = await key_indexer.get_or_create_cookie_key(cookie['name'])
                resp_cookies_batch.append((
                    db_id, key_id, cookie['value'],
                    cookie.get('domain'), cookie.get('path'), cookie.get('expires'),
                    cookie.get('http_only', False), cookie.get('secure', False),
                    cookie.get('same_site')
                ))

        # Insert headers and cookies
        if req_headers_batch:
            await conn.executemany(
                "INSERT INTO request_headers (request_id, key_id, value) VALUES (?, ?, ?)",
                req_headers_batch
            )

        if resp_headers_batch:
            await conn.executemany(
                "INSERT INTO response_headers (request_id, key_id, value) VALUES (?, ?, ?)",
                resp_headers_batch
            )

        if req_cookies_batch:
            await conn.executemany(
                "INSERT INTO request_cookies (request_id, key_id, value) VALUES (?, ?, ?)",
                req_cookies_batch
            )

        if resp_cookies_batch:
            await conn.executemany(
                """
                INSERT INTO response_cookies
                (request_id, key_id, value, domain, path, expires, http_only, secure, same_site)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                resp_cookies_batch
            )

        await conn.commit()

        return len(requests_batch)
