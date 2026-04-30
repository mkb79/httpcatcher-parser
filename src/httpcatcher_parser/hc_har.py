"""HAR export tool for HTTP Catcher sessions.

This module converts reverse-engineered HTTP Catcher session files into
a HAR 1.2 structure. It is intentionally lightweight and uses only the
standard library (argparse/json) for the CLI. Behavior remains the same
as the original script; changes are limited to English wording,
PEP 8/Ruff formatting, and Google-style docstrings.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlsplit

# ---- Import your scanner/parser -------------------------------------------
# Expected types must match your existing parser implementation:
#   HttpCatcherScanner, ConnectionFrame, RequestMainInfo, RequestHeader,
#   RequestBody, ResponseHeader, ResponseBody, Segment (for trailers).
from .hc_parser import (  # type: ignore
    ConnectionFrame,
    HttpCatcherScanner,
    RequestBody,
    RequestHeader,
    RequestMainInfo,
    ResponseBody,
    ResponseHeader,
    Segment,
)


# ============================================================================
# Utilities
# ============================================================================


def _iso(ts_ms: Optional[int]) -> Optional[str]:
    """Convert a millisecond epoch timestamp to an ISO-8601 string (UTC).

    Args:
        ts_ms: Milliseconds since Unix epoch or ``None``.

    Returns:
        ISO-8601 string in UTC (e.g., ``"2025-09-23T10:00:00+00:00"``) or ``None``
        if ``ts_ms`` is ``None`` or conversion fails.
    """
    if ts_ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None


def _split_headers_blob_list(payload: bytes) -> tuple[Optional[str], list[tuple[str, str]]]:
    """Split an HTTP header blob into start line and header pairs (preserves duplicates).

    Unlike dict-based parsing, this returns a list of ``(name, value)`` pairs in order,
    keeping duplicates such as multiple ``Set-Cookie`` headers.

    Args:
        payload: Raw header bytes (up to and including the CRLFCRLF separator).

    Returns:
        A tuple ``(start_line, headers)`` where:
          * ``start_line`` is the request/status line or ``None``
          * ``headers`` is a list of ``(name, value)`` pairs
    """
    try:
        head, _rest = payload.split(b"\r\n\r\n", 1)
    except ValueError:
        head = payload

    text = head.decode("latin-1", "replace")
    lines = text.split("\r\n")
    first = lines[0] if lines else None

    pairs: list[tuple[str, str]] = []
    for ln in lines[1:]:
        if not ln or ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        pairs.append((k.strip(), v.strip()))
    return first, pairs


def _headers_to_dict_multi(headers_list: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Build a multi-dict (``dict[str, list[str]]``) from a header pair list.

    Args:
        headers_list: Sequence of ``(name, value)`` header pairs.

    Returns:
        A dictionary mapping header names to a list of values, preserving duplicates.
    """
    out: dict[str, list[str]] = {}
    for k, v in headers_list:
        out.setdefault(k, []).append(v)
    return out


def _parse_req_startline(s: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse an HTTP request start line (method, target, http version).

    Args:
        s: The request line, e.g. ``"GET /path HTTP/1.1"``.

    Returns:
        Tuple ``(method, target, httpver)`` or ``(None, None, None)`` on failure.
    """
    if not s:
        return None, None, None
    parts = s.split(" ")
    if len(parts) < 3:
        return None, None, None
    return parts[0], parts[1], parts[2]  # method, url/path, httpver


def _parse_status_line(s: Optional[str]) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Parse an HTTP status line into its components.

    Args:
        s: The status line, e.g. ``"HTTP/1.1 200 OK"``.

    Returns:
        Tuple ``(status_code, reason, httpver)``. Returns ``(None, None, None)`` if
        parsing fails.
    """
    if not s or not s.startswith("HTTP/"):
        return None, None, None
    parts = s.split(" ", 2)
    if len(parts) < 2:
        return None, None, None
    code = int(parts[1]) if parts[1].isdigit() else None
    reason = parts[2] if len(parts) >= 3 else None
    return code, reason, parts[0]  # status, reason, httpver


def _maybe_brotli():
    """Try to import `brotli` and return the module if available.

    Returns:
        The ``brotli`` module or ``None`` if import fails.
    """
    try:
        import brotli  # type: ignore

        return brotli
    except Exception:
        return None


def _gunzip(data: bytes) -> bytes:
    """Decompress a gzip-compressed byte string.

    Args:
        data: Gzip-compressed data.

    Returns:
        Decompressed bytes.

    Raises:
        OSError: If gzip decoding fails.
    """
    import gzip
    import io

    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gf:
        return gf.read()


def _inflate(data: bytes) -> bytes:
    """Decompress data using zlib/deflate.

    Tries zlib with headers first, then raw deflate if that fails.

    Args:
        data: Deflate-compressed data.

    Returns:
        Decompressed bytes.
    """
    import zlib

    try:
        return zlib.decompress(data)
    except zlib.error:
        return zlib.decompress(data, -zlib.MAX_WBITS)


def _decode_content(content: bytes, content_encoding: Optional[str]) -> Tuple[bytes, Optional[int]]:
    """Decode HTTP response content according to ``Content-Encoding``.

    Args:
        content: Raw response body.
        content_encoding: The value of the ``Content-Encoding`` header (case-insensitive).

    Returns:
        Tuple ``(decoded_bytes, compression_delta)`` where ``compression_delta`` is the
        HAR ``content.compression`` value, defined as
        ``uncompressed_len - compressed_len``. Returns ``(content, None)`` when
        decoding fails.
    """
    if not content_encoding:
        return content, 0

    enc = content_encoding.lower()
    try:
        if "gzip" in enc:
            dec = _gunzip(content)
            return dec, len(dec) - len(content)
        if "deflate" in enc:
            dec = _inflate(content)
            return dec, len(dec) - len(content)
        if "br" in enc:
            brotli = _maybe_brotli()
            if brotli:
                dec = brotli.decompress(content)
                return dec, len(dec) - len(content)
            return content, None
    except Exception:
        return content, None
    return content, 0


def _bytes_as_text_or_b64(b: bytes) -> Tuple[str, Optional[str]]:
    """Render bytes as text when reasonable, otherwise base64-encode.

    Heuristic: if more than ~15% of bytes are non-printable ASCII, the output is
    returned as Base64 with ``encoding="base64"``; otherwise it's decoded as UTF-8
    (fallback to latin-1).

    Args:
        b: Input bytes.

    Returns:
        Tuple ``(text, encoding)`` where ``encoding`` is either ``None`` or ``"base64"``.
    """
    if not b:
        return "", None

    non_printable = sum((x < 9) or (x == 11) or (x == 12) or (x > 126) for x in b)
    ratio = non_printable / max(1, len(b))
    if ratio > 0.15:
        return base64.b64encode(b).decode("ascii"), "base64"

    try:
        return b.decode("utf-8"), None
    except UnicodeDecodeError:
        return b.decode("latin-1", "replace"), None


def _headers_size_from_payload(header_payload: Optional[bytes]) -> int:
    """Return the on-wire size of headers in bytes (including start line + CRLFs).

    In the session format this equals the header segment payload length.
    """
    if not header_payload:
        return 0
    return len(header_payload)


def _parse_request_cookies(headers_list: list[tuple[str, str]]) -> list[dict]:
    """Convert ``Cookie:`` headers to HAR cookie objects.

    Accepts multiple ``Cookie`` headers and splits semi-colon separated cookie pairs.

    Args:
        headers_list: List of ``(name, value)`` header pairs.

    Returns:
        A list of HAR cookie dicts with ``name`` and ``value`` keys.
    """
    out: list[dict] = []
    for k, v in headers_list:
        if k.lower() != "cookie":
            continue
        parts = [p.strip() for p in v.split(";") if p.strip()]
        for part in parts:
            if "=" not in part:
                continue
            name, val = part.split("=", 1)
            out.append({"name": name.strip(), "value": val.strip()})
    return out


def _parse_set_cookie(headers_list: list[tuple[str, str]]) -> list[dict]:
    """Parse ``Set-Cookie`` headers into HAR cookie objects.

    This is a minimal parser that handles common attributes:

    * ``Path``
    * ``Domain``
    * ``Expires`` (RFC 7231 date converted to UTC ISO string if possible)
    * ``Secure`` / ``HttpOnly``
    * ``SameSite``

    Args:
        headers_list: List of ``(name, value)`` header pairs.

    Returns:
        A list of HAR cookie dicts.
    """
    out: list[dict] = []
    for k, v in headers_list:
        if k.lower() != "set-cookie":
            continue

        first, *rest = [p.strip() for p in v.split(";")]
        if "=" not in first:
            continue

        cname, cval = first.split("=", 1)
        cobj: dict = {"name": cname.strip(), "value": cval.strip()}

        domain = path = same_site = None
        expires = None
        http_only = secure = False

        for attr in rest:
            if not attr:
                continue
            kv = attr.split("=", 1)
            aname = kv[0].strip().lower()
            aval = kv[1].strip() if len(kv) == 2 else None

            if aname == "path" and aval is not None:
                path = aval
            elif aname == "domain" and aval is not None:
                domain = aval
            elif aname == "expires" and aval is not None:
                try:
                    dt = parsedate_to_datetime(aval)
                    expires = dt.astimezone(timezone.utc).isoformat()
                except Exception:
                    expires = aval
            elif aname == "samesite" and aval is not None:
                same_site = aval
            elif aname == "httponly":
                http_only = True
            elif aname == "secure":
                secure = True
            # Max-Age/Priority/... are intentionally ignored for now

        if domain is not None:
            cobj["domain"] = domain
        if path is not None:
            cobj["path"] = path
        if expires is not None:
            cobj["expires"] = expires
        if http_only:
            cobj["httpOnly"] = True
        if secure:
            cobj["secure"] = True
        if same_site is not None:
            cobj["sameSite"] = same_site

        out.append(cobj)
    return out


def _build_url_and_qs(url_or_path: str, host: Optional[str]) -> tuple[str, list[dict]]:
    """Construct an absolute URL and its HAR ``queryString`` list from a path or URL.

    If ``url_or_path`` starts with ``/`` and a ``host`` is provided, an ``https://{host}{path}``
    URL is constructed. Otherwise, the input is assumed to be a full URL or left as-is.

    Args:
        url_or_path: Absolute URL or path.
        host: Hostname to use when ``url_or_path`` is a path.

    Returns:
        Tuple ``(url, queryStringList)`` where ``queryStringList`` is a list of HAR dicts.
    """
    if not url_or_path:
        return "/", []
    if url_or_path.startswith("/"):
        url_full = f"https://{host}{url_or_path}" if host else url_or_path
    else:
        url_full = url_or_path

    sp = urlsplit(url_full)
    qs_list = [{"name": k, "value": v} for (k, v) in parse_qsl(sp.query, keep_blank_values=True)]
    return url_full, qs_list


# ============================================================================
# Aggregators (per request_id)
# ============================================================================


@dataclass
class RequestAgg:
    """Aggregation container for request/response information of a single request_id."""

    # linkage
    request_id: int
    connection_id: Optional[int] = None

    # request
    req_header_ts: Optional[int] = None
    req_header: Optional[bytes] = None
    req_bodies: List[bytes] = field(default_factory=list)

    # response
    resp_header_ts: Optional[int] = None
    resp_header: Optional[bytes] = None
    resp_bodies: List[bytes] = field(default_factory=list)
    resp_last_ts: Optional[int] = None

    # rmi
    rmi_ts_leading: Optional[int] = None

    # derived
    method: Optional[str] = None
    path_or_url: Optional[str] = None
    httpver_req: Optional[str] = None
    status: Optional[int] = None
    reason: Optional[str] = None
    httpver_resp: Optional[str] = None


def _ensure_request(d: Dict[int, RequestAgg], request_id: int) -> RequestAgg:
    """Return the aggregator for ``request_id``, creating it if necessary."""
    a = d.get(request_id)
    if a is None:
        a = RequestAgg(request_id=request_id)
        d[request_id] = a
    return a


# ============================================================================
# HAR builder
# ============================================================================


def har_from_session(
    session_path: str,
    include_payload: bool = False,
    decompress_response: bool = True,
) -> dict:
    """Build a HAR 1.2 dictionary from a HTTP Catcher session file.

    Timings (approximate):
      * dns      = conn.ts2 - conn.ts1
      * connect  = (conn.ts_post2 or rmi.ts_leading if ts_post2==0) - conn.ts_post1
      * ssl      = rmi.ts_leading - conn.ts_post2 (if both present)
      * send     = 0 (no granular request-body timestamps available)
      * wait     = resp_header.ts_post - req_header.ts_post
      * receive  = (resp_final_or_last.ts_post) - resp_header.ts_post

    Missing endpoints result in ``None`` for the respective fields.

    Args:
        session_path: Path to the session file to parse.
        include_payload: If ``True``, include request/response bodies in the HAR.
        decompress_response: If ``True``, decompress response bodies based on
            ``Content-Encoding`` before storing them into HAR content.

    Returns:
        A dictionary representing a HAR 1.2 log with populated entries.
    """
    scanner = HttpCatcherScanner.default()

    # Connection frames and mapping connection_id -> frame
    connections: Dict[int, ConnectionFrame] = {}
    # request_id -> aggregation
    by_request: Dict[int, RequestAgg] = {}
    # Map request_id -> connection_id (from RMI)
    rmi_connection_map: Dict[int, int] = {}

    for item in scanner.scan(session_path):
        if isinstance(item, ConnectionFrame):
            connections[item.connection_id] = item

        elif isinstance(item, RequestMainInfo):
            a = _ensure_request(by_request, item.request_id)
            a.connection_id = a.connection_id or item.connection_id
            rmi_connection_map[item.request_id] = item.connection_id
            a.rmi_ts_leading = item.ts_leading

        elif isinstance(item, RequestHeader):
            a = _ensure_request(by_request, item.request_id)
            a.req_header_ts = a.req_header_ts or item.ts_post
            a.req_header = item.payload
            first, _ = _split_headers_blob_list(item.payload or b"")
            m, url, httpver = _parse_req_startline(first)
            a.method = m or a.method
            a.path_or_url = url or a.path_or_url
            a.httpver_req = httpver or a.httpver_req

        elif isinstance(item, RequestBody):
            a = _ensure_request(by_request, item.request_id)
            a.req_bodies.append(item.payload or b"")

        elif isinstance(item, ResponseHeader):
            a = _ensure_request(by_request, item.request_id)
            a.resp_header_ts = a.resp_header_ts or item.ts_post
            a.resp_header = item.payload
            first, _ = _split_headers_blob_list(item.payload or b"")
            status, reason, httpver = _parse_status_line(first)
            a.status = a.status or status
            a.reason = a.reason or reason
            a.httpver_resp = a.httpver_resp or httpver

        elif isinstance(item, ResponseBody):
            a = _ensure_request(by_request, item.request_id)
            a.resp_bodies.append(item.payload or b"")
            a.resp_last_ts = item.ts_post or a.resp_last_ts

        elif isinstance(item, Segment):
            # Trailers are currently ignored
            pass

    # HAR skeleton
    har: dict = {
        "log": {
            "version": "1.2",
            "creator": {"name": "httpcatcher-har-export", "version": "1.0"},
            "entries": [],
        }
    }

    for request_id, agg in by_request.items():
        conn = connections.get(agg.connection_id or rmi_connection_map.get(request_id))

        # ---- Request ------------------------------------------------------
        req_first, req_headers_list = _split_headers_blob_list(agg.req_header or b"")
        req_headers_multi = _headers_to_dict_multi(req_headers_list)
        method, url_or_path, httpver_req = _parse_req_startline(req_first)
        method = method or agg.method or "GET"
        url_or_path = url_or_path or agg.path_or_url or "/"

        host = conn.host if (conn and getattr(conn, "host", None)) else None
        url, query_string = _build_url_and_qs(url_or_path, host)

        req_raw = b"".join(agg.req_bodies) if agg.req_bodies else b""
        post_data = None
        if include_payload and req_raw:
            text, enc = _bytes_as_text_or_b64(req_raw)
            ct_list = req_headers_multi.get("Content-Type") or req_headers_multi.get(
                "content-type"
            ) or []
            mime = ct_list[0] if ct_list else ""
            post_data = {"mimeType": mime, "text": text}
            if enc:
                post_data["encoding"] = enc

        req_cookies = _parse_request_cookies(req_headers_list)
        har_req_headers = [{"name": k, "value": v} for (k, v) in req_headers_list]
        req_headers_size = _headers_size_from_payload(agg.req_header)

        # ---- Response -----------------------------------------------------
        resp_first, resp_headers_list = _split_headers_blob_list(agg.resp_header or b"")
        resp_headers_multi = _headers_to_dict_multi(resp_headers_list)
        status, reason, httpver_resp = _parse_status_line(resp_first)
        status = status or agg.status or 0
        reason = reason or agg.reason or ""

        resp_raw = b"".join(agg.resp_bodies) if agg.resp_bodies else b""
        ce_list = resp_headers_multi.get("Content-Encoding") or resp_headers_multi.get(
            "content-encoding"
        ) or []
        content_encoding = ce_list[0] if ce_list else None
        comp_delta: Optional[int] = 0
        body_out = resp_raw
        if decompress_response and resp_raw and content_encoding:
            body_out, comp_delta = _decode_content(resp_raw, content_encoding)

        ct_list = resp_headers_multi.get("Content-Type") or resp_headers_multi.get(
            "content-type"
        ) or []
        mime = ct_list[0] if ct_list else ""

        content_obj: dict = {
            "size": len(body_out),
            "mimeType": mime,
        }
        if include_payload and body_out is not None:
            text, enc = _bytes_as_text_or_b64(body_out)
            content_obj["text"] = text
            if enc:
                content_obj["encoding"] = enc
        if comp_delta is not None:
            content_obj["compression"] = comp_delta

        resp_cookies = _parse_set_cookie(resp_headers_list)
        har_resp_headers = [{"name": k, "value": v} for (k, v) in resp_headers_list]
        resp_headers_size = _headers_size_from_payload(agg.resp_header)

        # ---- Timings ------------------------------------------------------
        dns_ms = None
        if conn and conn.ts1 and conn.ts2:
            dns_ms = conn.ts2 - conn.ts1

        connect_ms = None
        if conn and conn.ts_post1 is not None:
            rhs = None
            if conn.ts_post2 and conn.ts_post2 > 0:
                rhs = conn.ts_post2
            elif agg.rmi_ts_leading:
                rhs = agg.rmi_ts_leading
            if rhs is not None:
                connect_ms = rhs - conn.ts_post1

        ssl_ms = None
        if agg.rmi_ts_leading and conn and conn.ts_post2 and conn.ts_post2 > 0:
            ssl_ms = agg.rmi_ts_leading - conn.ts_post2

        send_ms = 0
        anchor_req = agg.req_header_ts
        wait_ms = None
        if agg.resp_header_ts is not None and anchor_req is not None:
            wait_ms = agg.resp_header_ts - anchor_req

        receive_ms = None
        if agg.resp_last_ts is not None and agg.resp_header_ts is not None:
            receive_ms = agg.resp_last_ts - agg.resp_header_ts

        timings: dict = {
            "blocked": None,
            "dns": dns_ms,
            "connect": connect_ms,
            "ssl": ssl_ms,
            "send": send_ms,
            "wait": wait_ms,
            "receive": receive_ms,
        }

        started = _iso(agg.rmi_ts_leading or agg.req_header_ts)

        def _pos(x):  # noqa: ANN001 - local helper; type is validated at call sites
            return x if (isinstance(x, (int, float)) and x >= 0) else 0

        total_time = sum(_pos(timings[k]) for k in ("dns", "connect", "ssl", "send", "wait", "receive"))

        # redirectURL: take the first Location header if present
        loc = ""
        loc_list = resp_headers_multi.get("Location") or resp_headers_multi.get("location") or []
        if status and 300 <= status < 400 and loc_list:
            loc = loc_list[0]

        entry: dict = {
            "startedDateTime": started
            or _iso(agg.req_header_ts)
            or _iso(agg.resp_header_ts)
            or _iso(agg.resp_last_ts)
            or _iso(0),
            "time": total_time,
            "request": {
                "method": method,
                "url": url,
                "httpVersion": httpver_req or "HTTP/1.1",
                "cookies": req_cookies,
                "headers": har_req_headers,
                "queryString": query_string,
                "headersSize": req_headers_size,
                "bodySize": len(req_raw),
            },
            "response": {
                "status": status,
                "statusText": reason,
                "httpVersion": httpver_resp or "HTTP/1.1",
                "cookies": resp_cookies,
                "headers": har_resp_headers,
                "content": content_obj,
                "redirectURL": loc,
                "headersSize": resp_headers_size,
                "bodySize": len(body_out) if body_out is not None else 0,
            },
            "cache": {},
            "timings": timings,
        }

        if post_data is not None:
            entry["request"]["postData"] = post_data

        har["log"]["entries"].append(entry)

    return har


# ============================================================================
# CLI
# ============================================================================


def _default_outdir(session_path: str) -> str:
    """Return default output directory name for a given session path."""
    base = os.path.basename(session_path)
    stem, _ = os.path.splitext(base)
    return f"result_{stem}"


def main() -> None:
    """Command-line interface for exporting HAR from an HTTP Catcher session."""
    ap = argparse.ArgumentParser(description="Export HAR from HTTP Catcher session.")
    ap.add_argument("session", help="Path to session file")
    ap.add_argument("--outdir", help="Output directory (default: result_{basename})")
    ap.add_argument("--outfile", help="Output HAR file name (default: <basename>.har)")
    ap.add_argument(
        "--include-payload", action="store_true", help="Include request/response bodies in HAR"
    )
    ap.add_argument(
        "--no-decompress", action="store_true", help="Do not decompress response bodies"
    )
    args = ap.parse_args()

    outdir = args.outdir or _default_outdir(args.session)
    os.makedirs(outdir, exist_ok=True)
    base = os.path.basename(args.session)
    stem, _ = os.path.splitext(base)
    out_har = os.path.join(outdir, args.outfile or f"{stem}.har")

    har = har_from_session(
        args.session,
        include_payload=args.include_payload,
        decompress_response=not args.no_decompress,
    )
    with open(out_har, "w", encoding="utf-8") as f:
        json.dump(har, f, ensure_ascii=False, indent=2)
    print(f"Wrote HAR: {out_har}")


if __name__ == "__main__":
    main()
