"""Query engine for searching HTTP requests with flexible filters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .database import Database


class MatchMode(Enum):
    """String matching modes for filters."""

    EXACT = "exact"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    REGEX = "regex"


@dataclass
class TimeRange:
    """Time range filter."""

    start: Optional[int] = None  # Unix timestamp
    end: Optional[int] = None  # Unix timestamp


@dataclass
class HeaderFilter:
    """Header filter with key/value matching."""

    key: Optional[str] = None
    value: Optional[str] = None
    match_mode: MatchMode = MatchMode.CONTAINS
    request: bool = True  # True for request headers, False for response


@dataclass
class CookieFilter:
    """Cookie filter with key/value matching."""

    key: Optional[str] = None
    value: Optional[str] = None
    match_mode: MatchMode = MatchMode.CONTAINS
    request: bool = True  # True for request cookies, False for response


@dataclass
class SearchFilters:
    """All possible search filters."""

    # Basic filters
    method: Optional[str] = None
    url: Optional[str] = None
    url_match_mode: MatchMode = MatchMode.CONTAINS
    host: Optional[str] = None
    host_match_mode: MatchMode = MatchMode.CONTAINS
    path: Optional[str] = None
    path_match_mode: MatchMode = MatchMode.CONTAINS

    # Status code filters
    status_codes: Optional[list[int]] = None
    status_min: Optional[int] = None
    status_max: Optional[int] = None

    # Content-Type filters
    req_content_type: Optional[str] = None
    resp_content_type: Optional[str] = None
    resp_content_category: Optional[str] = None  # json, image, media, etc.

    # Time range
    time_range: Optional[TimeRange] = None

    # Header/Cookie filters
    headers: list[HeaderFilter] = field(default_factory=list)
    cookies: list[CookieFilter] = field(default_factory=list)

    # Body search
    body_search: Optional[str] = None  # FTS5 query
    body_in_request: bool = True
    body_in_response: bool = True

    # File filter
    file_ids: Optional[list[int]] = None

    # Pagination
    limit: int = 100
    offset: int = 0

    # Sorting
    sort_by: str = "req_timestamp"  # req_timestamp, resp_timestamp, duration_ms, status_code
    sort_desc: bool = True


class QueryEngine:
    """Build and execute search queries."""

    def __init__(self, db: Database):
        """Initialize query engine.

        Args:
            db: Database instance
        """
        self.db = db

    def search_requests(self, filters: SearchFilters) -> list[dict]:
        """Search requests with filters.

        Args:
            filters: Search filters

        Returns:
            List of request dicts with core fields
        """
        query, params = self._build_query(filters)

        cursor = self.db.conn.execute(query, params)
        results = []

        for row in cursor.fetchall():
            results.append({
                'id': row[0],
                'file_id': row[1],
                'method': row[2],
                'url': row[3],
                'host': row[4],
                'path': row[5],
                'status_code': row[6],
                'req_timestamp': row[7],
                'resp_timestamp': row[8],
                'duration_ms': row[9],
                'req_content_type': row[10],
                'resp_content_type': row[11],
                'resp_content_category': row[12],
                'req_body_size': row[13],
                'resp_body_size': row[14],
            })

        return results

    def count_requests(self, filters: SearchFilters) -> int:
        """Count requests matching filters.

        Args:
            filters: Search filters

        Returns:
            Total count of matching requests
        """
        # Build count query (similar to search but with COUNT)
        query, params = self._build_query(filters, count_only=True)
        cursor = self.db.conn.execute(query, params)
        return cursor.fetchone()[0]

    def _build_query(self, filters: SearchFilters, count_only: bool = False) -> tuple[str, list]:
        """Build SQL query from filters.

        Args:
            filters: Search filters
            count_only: If True, return COUNT(*) query

        Returns:
            Tuple of (query_string, params)
        """
        params: list[Any] = []
        where_clauses: list[str] = []
        joins: list[str] = []

        # Basic filters
        if filters.method:
            where_clauses.append("r.method = ?")
            params.append(filters.method)

        if filters.url:
            where_clauses.append(self._match_clause("r.url", filters.url_match_mode))
            params.append(self._match_param(filters.url, filters.url_match_mode))

        if filters.host:
            where_clauses.append(self._match_clause("r.host", filters.host_match_mode))
            params.append(self._match_param(filters.host, filters.host_match_mode))

        if filters.path:
            where_clauses.append(self._match_clause("r.path", filters.path_match_mode))
            params.append(self._match_param(filters.path, filters.path_match_mode))

        # Status code filters
        if filters.status_codes:
            placeholders = ','.join(['?'] * len(filters.status_codes))
            where_clauses.append(f"r.status_code IN ({placeholders})")
            params.extend(filters.status_codes)

        if filters.status_min is not None:
            where_clauses.append("r.status_code >= ?")
            params.append(filters.status_min)

        if filters.status_max is not None:
            where_clauses.append("r.status_code <= ?")
            params.append(filters.status_max)

        # Content-Type filters
        if filters.req_content_type:
            where_clauses.append("r.req_content_type LIKE ?")
            params.append(f"%{filters.req_content_type}%")

        if filters.resp_content_type:
            where_clauses.append("r.resp_content_type LIKE ?")
            params.append(f"%{filters.resp_content_type}%")

        if filters.resp_content_category:
            where_clauses.append("r.resp_content_category = ?")
            params.append(filters.resp_content_category)

        # Time range filters
        if filters.time_range:
            if filters.time_range.start:
                where_clauses.append("r.req_timestamp >= ?")
                params.append(filters.time_range.start)
            if filters.time_range.end:
                where_clauses.append("r.req_timestamp <= ?")
                params.append(filters.time_range.end)

        # File filters
        if filters.file_ids:
            placeholders = ','.join(['?'] * len(filters.file_ids))
            where_clauses.append(f"r.file_id IN ({placeholders})")
            params.extend(filters.file_ids)

        # Header filters
        for i, hf in enumerate(filters.headers):
            self._add_header_filter(hf, i, where_clauses, params, joins)

        # Cookie filters
        for i, cf in enumerate(filters.cookies):
            self._add_cookie_filter(cf, i, where_clauses, params, joins)

        # Body search (FTS5)
        if filters.body_search:
            self._add_body_search(filters, where_clauses, params, joins)

        # Build final query
        if count_only:
            select_clause = "SELECT COUNT(*)"
        else:
            select_clause = """
            SELECT
                r.id, r.file_id, r.method, r.url, r.host, r.path, r.status_code,
                r.req_timestamp, r.resp_timestamp, r.duration_ms,
                r.req_content_type, r.resp_content_type, r.resp_content_category,
                r.req_body_size, r.resp_body_size
            """

        query_parts = [select_clause, "FROM requests r"]

        # Add joins
        query_parts.extend(joins)

        # Add WHERE clause
        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))

        # Add sorting and pagination (not for count queries)
        if not count_only:
            # Sorting
            sort_column = f"r.{filters.sort_by}"
            sort_order = "DESC" if filters.sort_desc else "ASC"
            query_parts.append(f"ORDER BY {sort_column} {sort_order}")

            # Pagination
            query_parts.append(f"LIMIT {filters.limit} OFFSET {filters.offset}")

        return " ".join(query_parts), params

    def _match_clause(self, column: str, mode: MatchMode) -> str:
        """Generate SQL match clause."""
        if mode == MatchMode.EXACT:
            return f"{column} = ?"
        elif mode == MatchMode.CONTAINS:
            return f"{column} LIKE ?"
        elif mode == MatchMode.STARTS_WITH:
            return f"{column} LIKE ?"
        elif mode == MatchMode.ENDS_WITH:
            return f"{column} LIKE ?"
        elif mode == MatchMode.REGEX:
            return f"{column} REGEXP ?"
        else:
            return f"{column} LIKE ?"

    def _match_param(self, value: str, mode: MatchMode) -> str:
        """Generate parameter value for match mode."""
        if mode == MatchMode.EXACT:
            return value
        elif mode == MatchMode.CONTAINS:
            return f"%{value}%"
        elif mode == MatchMode.STARTS_WITH:
            return f"{value}%"
        elif mode == MatchMode.ENDS_WITH:
            return f"%{value}"
        elif mode == MatchMode.REGEX:
            return value
        else:
            return f"%{value}%"

    def _add_header_filter(
        self,
        hf: HeaderFilter,
        index: int,
        where_clauses: list[str],
        params: list,
        joins: list[str]
    ):
        """Add header filter to query."""
        table = "request_headers" if hf.request else "response_headers"
        alias = f"h{index}"

        # Join with header table
        joins.append(f"INNER JOIN {table} {alias} ON {alias}.request_id = r.id")

        # Key filter (using normalized key_id)
        if hf.key:
            # Join with header_keys for key lookup
            key_alias = f"hk{index}"
            joins.append(f"INNER JOIN header_keys {key_alias} ON {key_alias}.id = {alias}.key_id")
            where_clauses.append(f"{key_alias}.name_lower LIKE ?")
            params.append(f"%{hf.key.lower()}%")

        # Value filter
        if hf.value:
            where_clauses.append(self._match_clause(f"{alias}.value", hf.match_mode))
            params.append(self._match_param(hf.value, hf.match_mode))

    def _add_cookie_filter(
        self,
        cf: CookieFilter,
        index: int,
        where_clauses: list[str],
        params: list,
        joins: list[str]
    ):
        """Add cookie filter to query."""
        table = "request_cookies" if cf.request else "response_cookies"
        alias = f"c{index}"

        # Join with cookie table
        joins.append(f"INNER JOIN {table} {alias} ON {alias}.request_id = r.id")

        # Key filter (using normalized key_id)
        if cf.key:
            # Join with cookie_keys for key lookup
            key_alias = f"ck{index}"
            joins.append(f"INNER JOIN cookie_keys {key_alias} ON {key_alias}.id = {alias}.key_id")
            where_clauses.append(f"{key_alias}.name_lower LIKE ?")
            params.append(f"%{cf.key.lower()}%")

        # Value filter
        if cf.value:
            where_clauses.append(self._match_clause(f"{alias}.value", cf.match_mode))
            params.append(self._match_param(cf.value, cf.match_mode))

    def _add_body_search(
        self,
        filters: SearchFilters,
        where_clauses: list[str],
        params: list,
        joins: list[str]
    ):
        """Add FTS5 body search to query."""
        # Note: FTS5 virtual table is optional, check if it exists first
        # For now, use simple LIKE on body previews
        body_conditions = []

        if filters.body_in_request:
            body_conditions.append("r.req_body_preview LIKE ?")
            params.append(f"%{filters.body_search}%")

        if filters.body_in_response:
            body_conditions.append("r.resp_body_preview LIKE ?")
            params.append(f"%{filters.body_search}%")

        if body_conditions:
            where_clauses.append(f"({' OR '.join(body_conditions)})")

    def get_stats(self, file_id: Optional[int] = None) -> dict:
        """Get database statistics.

        Args:
            file_id: Optional file ID to filter stats

        Returns:
            Dict with statistics
        """
        where = "WHERE r.file_id = ?" if file_id else ""
        params = [file_id] if file_id else []

        stats = {}

        # Total requests
        cursor = self.db.conn.execute(
            f"SELECT COUNT(*) FROM requests r {where}",
            params
        )
        stats['total_requests'] = cursor.fetchone()[0]

        # Requests by method
        cursor = self.db.conn.execute(
            f"""
            SELECT method, COUNT(*) as count
            FROM requests r {where}
            GROUP BY method
            ORDER BY count DESC
            """,
            params
        )
        stats['by_method'] = {row[0]: row[1] for row in cursor.fetchall()}

        # Requests by status code
        cursor = self.db.conn.execute(
            f"""
            SELECT status_code, COUNT(*) as count
            FROM requests r {where}
            GROUP BY status_code
            ORDER BY count DESC
            LIMIT 20
            """,
            params
        )
        stats['by_status'] = {row[0]: row[1] for row in cursor.fetchall()}

        # Requests by content category
        cursor = self.db.conn.execute(
            f"""
            SELECT resp_content_category, COUNT(*) as count
            FROM requests r {where}
            GROUP BY resp_content_category
            ORDER BY count DESC
            """,
            params
        )
        stats['by_content_category'] = {row[0]: row[1] for row in cursor.fetchall()}

        # Top hosts
        cursor = self.db.conn.execute(
            f"""
            SELECT host, COUNT(*) as count
            FROM requests r {where}
            GROUP BY host
            ORDER BY count DESC
            LIMIT 10
            """,
            params
        )
        stats['top_hosts'] = {row[0]: row[1] for row in cursor.fetchall()}

        # Time range
        cursor = self.db.conn.execute(
            f"""
            SELECT MIN(req_timestamp), MAX(req_timestamp)
            FROM requests r {where}
            """,
            params
        )
        row = cursor.fetchone()
        stats['time_range'] = {
            'start': row[0],
            'end': row[1]
        }

        # Average duration
        cursor = self.db.conn.execute(
            f"""
            SELECT AVG(duration_ms), MIN(duration_ms), MAX(duration_ms)
            FROM requests r
            {where}
            """,
            params
        )
        row = cursor.fetchone()
        stats['duration'] = {
            'avg_ms': row[0],
            'min_ms': row[1],
            'max_ms': row[2]
        }

        # Body sizes
        cursor = self.db.conn.execute(
            f"""
            SELECT
                SUM(req_body_size), SUM(resp_body_size),
                AVG(req_body_size), AVG(resp_body_size)
            FROM requests r {where}
            """,
            params
        )
        row = cursor.fetchone()
        stats['body_sizes'] = {
            'total_req_bytes': row[0] or 0,
            'total_resp_bytes': row[1] or 0,
            'avg_req_bytes': row[2] or 0,
            'avg_resp_bytes': row[3] or 0,
        }

        return stats

    def get_available_keys(self, key_type: str = "header") -> list[dict]:
        """Get all available header or cookie keys.

        Args:
            key_type: 'header' or 'cookie'

        Returns:
            List of key dicts with name and usage count
        """
        table = "header_keys" if key_type == "header" else "cookie_keys"

        cursor = self.db.conn.execute(
            f"""
            SELECT name, usage_count
            FROM {table}
            ORDER BY usage_count DESC
            """
        )

        return [
            {'name': row[0], 'usage_count': row[1]}
            for row in cursor.fetchall()
        ]

    def autocomplete_key(self, prefix: str, key_type: str = "header", limit: int = 20) -> list[str]:
        """Autocomplete header or cookie key names.

        Args:
            prefix: Key name prefix
            key_type: 'header' or 'cookie'
            limit: Maximum results

        Returns:
            List of matching key names
        """
        table = "header_keys" if key_type == "header" else "cookie_keys"

        cursor = self.db.conn.execute(
            f"""
            SELECT name
            FROM {table}
            WHERE name_lower LIKE ?
            ORDER BY usage_count DESC
            LIMIT ?
            """,
            (f"{prefix.lower()}%", limit)
        )

        return [row[0] for row in cursor.fetchall()]
