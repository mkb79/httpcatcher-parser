"""HTTP Catcher session parser.

This module parses HTTP Catcher binary session files and provides utilities to
inspect requests, responses and connection frames. The code is formatted per
PEP 8 and aims to be Ruff-friendly. Comments and docstrings are in English.
"""

from __future__ import annotations

import argparse
import json
import mmap
import os
import struct
from dataclasses import dataclass, field
from typing import Iterator, List, Literal, Optional, Protocol, Set, Tuple, Union

# ==========
# Binary helpers & constants
# ==========

BE_U16 = struct.Struct(">H")
BE_U24_PAD = struct.Struct(">I")
BE_U32 = struct.Struct(">I")
BE_U64 = struct.Struct(">Q")

MAGIC_FILE_HEADER = 0x0100

MARK_RMI = 0x00000702
MARK_REQ_SEGMENT = 0x00000C05
MARK_RESP_SEGMENT = 0x00000C08
MARK_CONN_TAIL = 0x00001801

TAIL_FOOTER_SIZE = 4 + 4 + 8 + 1 + 3  # 20
TLS_MARKER_STATUS = 0x0000080A

DAY_MS = 24 * 60 * 60 * 1000

ReqRespKind = Literal["request", "response"]
Subtype = Literal["header", "body_mid", "body_final", "trailer", "unknown"]


def u16(mv: memoryview, off: int) -> int:
    return BE_U16.unpack_from(mv, off)[0]


def u24(mv: memoryview, off: int) -> int:
    return BE_U24_PAD.unpack_from(mv, off - 1)[0] & 0xFFFFFF


def u32(mv: memoryview, off: int) -> int:
    return BE_U32.unpack_from(mv, off)[0]


def u64(mv: memoryview, off: int) -> int:
    return BE_U64.unpack_from(mv, off)[0]


def _hex_preview(b: bytes, max_n: int = 32) -> Tuple[str, str]:
    """Return a small hex & ASCII preview for diagnostics."""
    s = b[:max_n]
    hexstr = " ".join(f"{x:02X}" for x in s)
    asciistr = "".join(chr(x) if 32 <= x < 127 else "." for x in s)
    return hexstr, asciistr


# ==========
# Data models
# ==========


@dataclass(slots=True)
class FileHeader:
    magic: int


@dataclass(slots=True)
class ConnectionFrame:
    start: int
    end: int
    connection_id: int
    ts1: int
    ts2: int
    port: int
    host: str
    ip_bytes: bytes
    ts_post1: int
    ts_post2: int

    def __repr__(self) -> str:
        return (
            "ConnectionFrame("
            f"start=0x{self.start:X}, end=0x{self.end:X}, "
            f"connection_id={self.connection_id}, ts1={self.ts1}, ts2={self.ts2}, "
            f"port={self.port}, host={self.host!r}, "
            f"ip_len={len(self.ip_bytes)}, "
            f"ts_post1={self.ts_post1}, ts_post2={self.ts_post2})"
        )


@dataclass(slots=True)
class RequestMainInfo:
    start: int
    end: int
    request_id: int
    flags: int
    proto: int
    connection_id: int
    ts_leading: Optional[int]

    def __repr__(self) -> str:
        return (
            "RequestMainInfo("
            f"start=0x{self.start:X}, end=0x{self.end:X}, "
            f"request_id={self.request_id}, flags=0x{self.flags:04X}, "
            f"proto={self.proto}, connection_id={self.connection_id}, "
            f"ts_leading={self.ts_leading})"
        )


# --- New typed segment hierarchy ------------------------------------------


@dataclass(slots=True)
class _SegmentBase:
    """
    Common base for all parsed segments.

    This mirrors the previous `Segment` fields to preserve downstream
    compatibility in logging and exports.
    """

    start: int
    end: int
    request_id: int
    status: Optional[int]
    length: int
    ts_post: Optional[int]
    truncated: bool
    missing_bytes: int
    payload: Optional[bytes]

    @property
    def kind(self) -> ReqRespKind:  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def subtype(self) -> Subtype:  # pragma: no cover - abstract
        raise NotImplementedError

    def _repr_core(self) -> str:
        pl = 0 if self.payload is None else len(self.payload)
        return (
            f"start=0x{self.start:X}, end=0x{self.end:X}, "
            f"request_id={self.request_id}, status={self.status}, "
            f"length={self.length}, ts_post={self.ts_post}, "
            f"truncated={self.truncated}, "
            f"missing_bytes={self.missing_bytes}, payload_len={pl}"
        )


@dataclass(slots=True)
class RequestHeader(_SegmentBase):
    """HTTP request header record (status==3)."""

    @property
    def kind(self) -> ReqRespKind:
        return "request"

    @property
    def subtype(self) -> Subtype:
        return "header"

    def __repr__(self) -> str:
        return f"RequestHeader({self._repr_core()})"


@dataclass(slots=True)
class RequestBody(_SegmentBase):
    """HTTP request body chunk (status==4)."""

    @property
    def kind(self) -> ReqRespKind:
        return "request"

    @property
    def subtype(self) -> Subtype:
        return "body_mid"

    def __repr__(self) -> str:
        return f"RequestBody({self._repr_core()})"


@dataclass(slots=True)
class ResponseHeader(_SegmentBase):
    """HTTP response header record (status==6)."""

    @property
    def kind(self) -> ReqRespKind:
        return "response"

    @property
    def subtype(self) -> Subtype:
        return "header"

    def __repr__(self) -> str:
        return f"ResponseHeader({self._repr_core()})"


@dataclass(slots=True)
class ResponseBody(_SegmentBase):
    """HTTP response body chunk. `status` 7 (mid) or 9 (final)."""

    final: bool = False  # convenience flag to quickly identify final bodies

    @property
    def kind(self) -> ReqRespKind:
        return "response"

    @property
    def subtype(self) -> Subtype:
        return "body_final" if self.final else "body_mid"

    def __repr__(self) -> str:
        endflag = ", final=True" if self.final else ""
        return f"ResponseBody({self._repr_core()}{endflag})"


@dataclass(slots=True)
class Segment:
    """
    Kept for trailer (status==8) and tail-only/unknown footer matches.

    This preserves the name you asked to retain for trailers while all
    other request/response pieces get their own classes.
    """

    start: int
    end: int
    kind: ReqRespKind
    request_id: int
    status: Optional[int]
    subtype: Subtype
    length: int
    ts_post: Optional[int]
    truncated: bool
    missing_bytes: int
    payload: Optional[bytes]

    def __repr__(self) -> str:
        pl = 0 if (self.payload is None) else len(self.payload)
        return (
            "Segment("
            f"start=0x{self.start:X}, end=0x{self.end:X}, "
            f"kind={self.kind}, request_id={self.request_id}, status={self.status}, "
            f"subtype={self.subtype}, length={self.length}, "
            f"ts_post={self.ts_post}, truncated={self.truncated}, "
            f"missing_bytes={self.missing_bytes}, payload_len={pl})"
        )


@dataclass(slots=True)
class UnknownChunk:
    start: int
    end: int
    sample: bytes = b""

    def __repr__(self) -> str:
        return f"UnknownChunk(start=0x{self.start:X}, end=0x{self.end:X})"


@dataclass(slots=True)
class ConnectTunnelBytes:
    start: int
    end: int
    request_id: int

    def __repr__(self) -> str:
        return (
            "ConnectTunnelBytes("
            f"start=0x{self.start:X}, end=0x{self.end:X}, request_id={self.request_id})"
        )


@dataclass(slots=True)
class TimestampMark:
    """Loose TS64 marker recognized only when a plausible range exists."""

    start: int
    end: int
    ts: int

    def __repr__(self) -> str:
        return (
            f"TimestampMark(start=0x{self.start:X}, end=0x{self.end:X}, ts={self.ts})"
        )


ParsedItem = Union[
    FileHeader,
    ConnectionFrame,
    RequestMainInfo,
    RequestHeader,
    RequestBody,
    ResponseHeader,
    ResponseBody,
    Segment,  # trailers + tail-only unknowns
    UnknownChunk,
    ConnectTunnelBytes,
    TimestampMark,
]


# ==========
# Exceptions
# ==========


class ParseError(RuntimeError):
    """Base parse error."""


class MagicMismatch(ParseError):
    """Magic header mismatch."""


class Bounds(ParseError):
    """Read beyond file bounds."""


class InvalidFooter(ParseError):
    """Invalid segment footer layout."""


class InvalidProlog(ParseError):
    """Invalid segment prolog layout."""


class ConnFrameInvalid(ParseError):
    """Invalid connection frame."""


# ==========
# Reader
# ==========


class Reader:
    """Cursor + bounds-checked access over a memoryview."""

    __slots__ = ("mv", "pos", "size")

    def __init__(self, mv: memoryview):
        self.mv = mv
        self.pos = 0
        self.size = len(mv)

    def remaining(self) -> int:
        return self.size - self.pos

    def require(self, n: int) -> None:
        if self.pos + n > self.size:
            raise Bounds(f"need {n} bytes, have {self.remaining()} at 0x{self.pos:X}")

    def peek_u32(self, rel: int = 0) -> Optional[int]:
        if self.pos + rel + 4 > self.size:
            return None
        return u32(self.mv, self.pos + rel)

    def peek_u64(self, rel: int = 0) -> Optional[int]:
        if self.pos + rel + 8 > self.size:
            return None
        return u64(self.mv, self.pos + rel)

    def slice(self, n: int) -> memoryview:
        self.require(n)
        s = self.mv[self.pos : self.pos + n]
        self.pos += n
        return s

    def skip(self, n: int) -> None:
        self.require(n)
        self.pos += n

    def align_to(self, new_pos: int) -> None:
        if new_pos < self.pos or new_pos > self.size:
            raise Bounds(f"align_to out of range: 0x{new_pos:X}")
        self.pos = new_pos


# ==========
# Parse context
# ==========


@dataclass(slots=True)
class ParseCtx:
    """
    Mutable parse context accumulated while scanning.

    baseline_ts:
        Wird aus dem ersten ConnectionFrame.ts1 gesetzt.
        Alle Zeitstempel sind nur gültig, wenn
        baseline_ts <= ts <= baseline_ts + future_limit_ms.
    """

    seen_conn: Set[int] = field(default_factory=set)
    seen_rmi: Set[int] = field(default_factory=set)
    seen_req_header: Set[int] = field(default_factory=set)
    seen_resp_header: Set[int] = field(default_factory=set)
    resp_body_open: Set[int] = field(default_factory=set)
    connect_req: Set[int] = field(default_factory=set)
    connect_established: Set[int] = field(default_factory=set)

    ts_seen_min: Optional[int] = None  # ms since epoch
    ts_seen_max: Optional[int] = None

    baseline_ts: Optional[int] = None
    future_limit_ms: int = DAY_MS


def _ts_in_window(ctx: ParseCtx, ts: int) -> bool:
    """
    True, wenn eine Baseline existiert und ts innerhalb
    [baseline, baseline + future_limit_ms] liegt.
    """
    if ctx.baseline_ts is None:
        return False
    return ctx.baseline_ts <= ts <= ctx.baseline_ts + ctx.future_limit_ms


def _observe_ts(ctx: ParseCtx, ts: Optional[int]) -> None:
    if ts is None:
        return
    if ctx.ts_seen_min is None or ts < ctx.ts_seen_min:
        ctx.ts_seen_min = ts
    if ctx.ts_seen_max is None or ts > ctx.ts_seen_max:
        ctx.ts_seen_max = ts


def _is_connect_request_header(item: Union[RequestHeader, Segment]) -> bool:
    """Detect CONNECT method in request headers (typed or legacy Segment)."""
    payload = getattr(item, "payload", None)
    if not payload:
        return False
    p = payload
    if p.startswith(b"CONNECT "):
        return True
    if p[:1].lstrip(b"\x00").startswith(b"CONNECT "):
        return True
    if p[:2].lstrip(b"\x00").startswith(b"CONNECT "):
        return True
    return False


def _is_connect_200_response_header(
    item: Union[ResponseHeader, Segment],
) -> bool:
    payload = getattr(item, "payload", None)
    if not payload:
        return False
    p = payload
    return p.startswith(b"HTTP/1.1 200") or p.startswith(b"HTTP/1.0 200")


# ==========
# Parser plugin interface
# ==========


class BaseParser(Protocol):
    """
    Parser protocol for pluggable binary recognizers.

    A parser must:
      - Inspect bytes at the current reader position.
      - If a valid item is recognized, advance the reader cursor accordingly
        and return the decoded `ParsedItem`.
      - Otherwise, leave the cursor untouched and return `None`.

    Parsers should be pure decoders; they must NOT mutate `ParseCtx`.
    The scanner centrally updates `ParseCtx` after an item is emitted.
    """

    name: str
    priority: int  # smaller runs earlier

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]: ...


# ==========
# Concrete parsers
# ==========


class FileHeaderParser:
    name = "file_header"
    priority = 0

    def __init__(self) -> None:
        self._done = False

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        if self._done:
            return None
        if r.pos != 0:
            self._done = True
            return None
        r.require(2)
        magic = u16(r.mv, r.pos)
        if magic != MAGIC_FILE_HEADER:
            raise MagicMismatch(
                f"expected 0x{MAGIC_FILE_HEADER:04X}, got 0x{magic:04X}"
            )
        r.skip(2)
        self._done = True
        return FileHeader(magic=magic)


class RMIWithLeadingTsParser:
    name = "rmi_with_ts"
    priority = 10

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        # Need baseline to validate ts_leading
        if ctx.baseline_ts is None:
            return None

        need = 8 + 4 + 4 + 2 + 1 + 4
        if r.remaining() < need:
            return None

        mv, size, start = r.mv, r.size, r.pos
        p = start

        ts_lead = u64(mv, p)
        p += 8
        if not _ts_in_window(ctx, ts_lead):
            return None

        if u32(mv, p) != MARK_RMI:
            return None
        p += 4

        if p + (4 + 2 + 1 + 4) > size:
            return None

        request_id = u32(mv, p)
        p += 4
        flags = u16(mv, p)
        p += 2
        proto = mv[p]
        p += 1
        connection_id = u32(mv, p)
        p += 4

        r.align_to(p)
        return RequestMainInfo(
            start=start,
            end=p,
            request_id=request_id,
            flags=flags,
            proto=proto,
            connection_id=connection_id,
            ts_leading=ts_lead,
        )


class RMIParser:
    name = "rmi"
    priority = 20

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        mv, size, start = r.mv, r.size, r.pos
        if r.remaining() < 4 + 4 + 2 + 1 + 4:
            return None
        p = start
        if u32(mv, p) != MARK_RMI:
            return None
        p += 4
        if p + (4 + 2 + 1 + 4) > size:
            return None
        request_id = u32(mv, p)
        p += 4
        flags = u16(mv, p)
        p += 2
        proto = mv[p]
        p += 1
        connection_id = u32(mv, p)
        p += 4
        r.align_to(p)
        return RequestMainInfo(
            start=start,
            end=p,
            request_id=request_id,
            flags=flags,
            proto=proto,
            connection_id=connection_id,
            ts_leading=None,
        )


class ReqRespSegmentParser:
    name = "segment"
    priority = 30

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        mv, size, start = r.mv, r.size, r.pos
        if r.remaining() < (3 + 1 + 4):
            return None
        p = start
        len_prolog = int.from_bytes(mv[p : p + 3], "big")
        p += 3
        status = mv[p]
        p += 1
        if p + 4 > size:
            return None
        request_id = u32(mv, p)
        p += 4

        if status not in (3, 4, 6, 7, 8, 9):
            return None

        # Zero-length final-body fast path
        if len_prolog == 0 and status == 9 and (p + 8) <= size:
            req_id_rep2 = u32(mv, p + 4)
            if req_id_rep2 == request_id:
                r.align_to(p + 8)
                return ResponseBody(
                    start=start,
                    end=r.pos,
                    request_id=request_id,
                    status=9,
                    length=0,
                    ts_post=None,
                    truncated=False,
                    missing_bytes=0,
                    payload=b"",
                    final=True,
                )

        if len_prolog == 0 and status == 9:
            if (request_id in ctx.resp_body_open) or (
                request_id in ctx.seen_resp_header
            ):
                r.align_to(p)
                return ResponseBody(
                    start=start,
                    end=r.pos,
                    request_id=request_id,
                    status=9,
                    length=0,
                    ts_post=None,
                    truncated=False,
                    missing_bytes=0,
                    payload=b"",
                    final=True,
                )

        min_footer = 4 + 4 + 8 + 1 + 3
        payload_start = p
        payload_end_nominal = payload_start + len_prolog
        footer_end_nominal = payload_end_nominal + min_footer
        if footer_end_nominal > size:
            return None

        end_marker = u32(mv, payload_end_nominal)
        if end_marker not in (MARK_REQ_SEGMENT, MARK_RESP_SEGMENT):
            return None

        q = payload_end_nominal + 4
        req_id_rep = u32(mv, q)
        q += 4
        ts_post = u64(mv, q)
        q += 8
        pad = mv[q]
        q += 1
        len_repeat = int.from_bytes(mv[q : q + 3], "big")
        q += 3
        if pad != 0x00 or req_id_rep != request_id or len_repeat != len_prolog:
            return None

        kind: ReqRespKind = "request" if end_marker == MARK_REQ_SEGMENT else "response"

        payload = mv[payload_start:payload_end_nominal].tobytes()
        ts_post_valid = ts_post if _ts_in_window(ctx, ts_post) else None

        if kind == "request":
            if status == 3:
                r.align_to(q)
                return RequestHeader(
                    start=start,
                    end=q,
                    request_id=request_id,
                    status=3,
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                )
            if status == 4:
                r.align_to(q)
                return RequestBody(
                    start=start,
                    end=q,
                    request_id=request_id,
                    status=4,
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                )
            return None
        else:
            if status == 6:
                r.align_to(q)
                return ResponseHeader(
                    start=start,
                    end=q,
                    request_id=request_id,
                    status=6,
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                )
            if status == 7:
                r.align_to(q)
                return ResponseBody(
                    start=start,
                    end=q,
                    request_id=request_id,
                    status=7,
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                    final=False,
                )
            if status == 9:
                r.align_to(q)
                return ResponseBody(
                    start=start,
                    end=q,
                    request_id=request_id,
                    status=9,
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                    final=True,
                )
            if status == 8:
                r.align_to(q)
                return Segment(
                    start=start,
                    end=q,
                    kind="response",
                    request_id=request_id,
                    status=8,
                    subtype="trailer",
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                )
            return None


class TLSMarkerParser:
    name = "tls_marker"
    priority = 40

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        if r.remaining() < 16:
            return None
        mv, start = r.mv, r.pos
        status = u32(mv, start)
        if status != TLS_MARKER_STATUS:
            return None
        request_id = u32(mv, start + 4)
        # ts64 = u64(mv, start + 8)  # derzeit nicht genutzt
        if (request_id not in ctx.seen_rmi) and (request_id not in ctx.connect_req):
            return None
        r.align_to(start + 16)
        return ConnectTunnelBytes(start=start, end=r.pos, request_id=request_id)


class ReqRespWithLeadingTsParser:
    name = "segment_with_ts"
    priority = 50

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        # Need baseline to validate leading ts
        if ctx.baseline_ts is None:
            return None

        mv, size, start = r.mv, r.size, r.pos
        if r.remaining() < 36:
            return None
        p = start
        ts_lead = u64(mv, p)
        p += 8

        if not _ts_in_window(ctx, ts_lead):
            return None

        len_prolog = int.from_bytes(mv[p : p + 3], "big")
        p += 3
        if p + 1 + 4 > size:
            return None
        status = mv[p]
        p += 1
        request_id = u32(mv, p)
        p += 4
        if status not in (3, 4, 6, 7, 8, 9):
            return None

        min_footer = 4 + 4 + 8 + 1 + 3
        payload_start = p
        payload_end_nominal = payload_start + len_prolog
        footer_end_nominal = payload_end_nominal + min_footer
        if footer_end_nominal > size:
            return None

        end_marker = u32(mv, payload_end_nominal)
        if end_marker not in (MARK_REQ_SEGMENT, MARK_RESP_SEGMENT):
            return None

        q = payload_end_nominal + 4
        req_id_rep = u32(mv, q)
        q += 4
        ts_post = u64(mv, q)
        q += 8
        pad = mv[q]
        q += 1
        len_repeat = int.from_bytes(mv[q : q + 3], "big")
        q += 3
        if pad != 0x00 or req_id_rep != request_id or len_repeat != len_prolog:
            return None

        payload = mv[payload_start:payload_end_nominal].tobytes()
        kind: ReqRespKind = "request" if end_marker == MARK_REQ_SEGMENT else "response"
        ts_post_valid = ts_post if _ts_in_window(ctx, ts_post) else None

        if kind == "request":
            if status == 3:
                r.align_to(q)
                return RequestHeader(
                    start=start,
                    end=q,
                    request_id=request_id,
                    status=3,
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                )
            if status == 4:
                r.align_to(q)
                return RequestBody(
                    start=start,
                    end=q,
                    request_id=request_id,
                    status=4,
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                )
            return None
        else:
            if status == 6:
                r.align_to(q)
                return ResponseHeader(
                    start=start,
                    end=q,
                    request_id=request_id,
                    status=6,
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                )
            if status == 7:
                r.align_to(q)
                return ResponseBody(
                    start=start,
                    end=q,
                    request_id=request_id,
                    status=7,
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                    final=False,
                )
            if status == 9:
                r.align_to(q)
                return ResponseBody(
                    start=start,
                    end=q,
                    request_id=request_id,
                    status=9,
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                    final=True,
                )
            if status == 8:
                r.align_to(q)
                return Segment(
                    start=start,
                    end=q,
                    kind="response",
                    request_id=request_id,
                    status=8,
                    subtype="trailer",
                    length=len_prolog,
                    ts_post=ts_post_valid,
                    truncated=False,
                    missing_bytes=0,
                    payload=payload,
                )
            return None


class ConnectionParser:
    name = "connection"
    priority = 60

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        mv, size, start = r.mv, r.size, r.pos
        p = start
        if r.remaining() < 54:
            return None
        if p + 3 > size:
            return None
        p += 3
        if p + 1 > size:
            return None
        pad = mv[p]
        p += 1
        if pad != 0x00:
            return None
        if p + 4 > size:
            return None
        conn_id1 = u32(mv, p)
        p += 4
        if p + 8 + 8 > size:
            return None
        ts1 = u64(mv, p)
        p += 8
        ts2 = u64(mv, p)
        p += 8
        if p + 2 > size:
            return None
        port = u16(mv, p)
        p += 2
        if p + 2 > size:
            return None
        host_len = u16(mv, p)
        p += 2
        if host_len == 0 or p + host_len > size:
            return None
        try:
            host_str = mv[p : p + host_len].tobytes().decode("utf-8", "strict")
        except Exception:
            return None
        p += host_len
        if p + 2 > size:
            return None
        ip_len = u16(mv, p)
        p += 2
        if ip_len == 0 or p + ip_len > size:
            return None
        ip_bytes = mv[p : p + ip_len].tobytes()
        p += ip_len
        if p + 4 + 4 > size:
            return None
        marker = u32(mv, p)
        p += 4
        if marker != MARK_CONN_TAIL:
            return None
        conn_id2 = u32(mv, p)
        p += 4
        if conn_id2 != conn_id1:
            return None
        if p + 8 + 8 > size:
            return None
        ts_post1 = u64(mv, p)
        p += 8
        ts_post2 = u64(mv, p)
        p += 8

        obj = ConnectionFrame(
            start=start,
            end=p,
            connection_id=conn_id2,
            ts1=ts1,
            ts2=ts2,
            port=port,
            host=host_str,
            ip_bytes=ip_bytes,
            ts_post1=ts_post1,
            ts_post2=ts_post2,
        )

        # Set baseline on the very first ConnectionFrame
        if ctx.baseline_ts is None:
            ctx.baseline_ts = ts1

        r.align_to(p)
        return obj


class TailOnlyParser:
    name = "tail_only"
    priority = 70

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        if r.remaining() < TAIL_FOOTER_SIZE:
            return None
        mv, start = r.mv, r.pos
        end_marker = u32(mv, start)
        if end_marker not in (MARK_REQ_SEGMENT, MARK_RESP_SEGMENT):
            return None
        p = start + 4
        if p + (4 + 8 + 1 + 3) > r.size:
            return None
        request_id = u32(mv, p)
        p += 4
        ts_post = u64(mv, p)
        p += 8
        pad = mv[p]
        p += 1
        if pad != 0x00:
            return None
        len_repeat = int.from_bytes(mv[p : p + 3], "big")
        p += 3

        if request_id in ctx.connect_req:
            r.align_to(p)
            return ConnectTunnelBytes(start=start, end=p, request_id=request_id)

        kind: ReqRespKind = "request" if end_marker == MARK_REQ_SEGMENT else "response"
        r.align_to(p)
        ts_post_valid = ts_post if _ts_in_window(ctx, ts_post) else None
        return Segment(
            start=start,
            end=p,
            kind=kind,
            request_id=request_id,
            status=None,
            subtype="unknown",
            length=len_repeat,
            ts_post=ts_post_valid,
            truncated=True,
            missing_bytes=len_repeat,
            payload=None,
        )


class PlausibleTs64Parser:
    name = "plausible_ts64"
    priority = 90

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        if ctx.baseline_ts is None:
            return None
        if r.remaining() < 8:
            return None
        mv, start = r.mv, r.pos
        ts = u64(mv, start)

        if not _ts_in_window(ctx, ts):
            return None

        r.align_to(start + 8)
        return TimestampMark(start=start, end=r.pos, ts=ts)


class FallbackUnknownParser:
    """Advance by one byte and emit a small UnknownChunk preview."""

    name = "unknown"
    priority = 9999

    def __init__(self, sample_len: int = 32) -> None:
        self.sample_len = sample_len

    def parse(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        unk_start = r.pos
        r.skip(1)
        sample = bytes(r.mv[unk_start : min(unk_start + self.sample_len, len(r.mv))])
        return UnknownChunk(start=unk_start, end=r.pos, sample=sample)


# ==========
# Scanner
# ==========


class HttpCatcherScanner:
    """
    Pluggable scanner for HTTP Catcher session files.

    Parsers are pure decoders; scanner updates `ParseCtx` centrally to keep
    side effects and derived state consistent.
    """

    def __init__(self, parsers: Optional[List[BaseParser]] = None) -> None:
        self.parsers: List[BaseParser] = sorted(
            parsers or [], key=lambda p: (p.priority, p.name)
        )

    def register(self, parser: BaseParser) -> None:
        self.parsers.append(parser)
        self.parsers.sort(key=lambda p: (p.priority, p.name))

    def unregister(self, name: str) -> None:
        self.parsers = [p for p in self.parsers if p.name != name]

    @classmethod
    def default(cls) -> "HttpCatcherScanner":
        return cls(
            [
                FileHeaderParser(),
                RMIWithLeadingTsParser(),
                RMIParser(),
                ReqRespSegmentParser(),
                TLSMarkerParser(),
                ReqRespWithLeadingTsParser(),
                ConnectionParser(),
                TailOnlyParser(),
                PlausibleTs64Parser(),
                FallbackUnknownParser(),
            ]
        )

    def scan(self, path: str) -> Iterator[ParsedItem]:
        file_size = os.path.getsize(path)
        with open(path, "rb") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            mv = memoryview(mm)
            try:
                r = Reader(mv)
                ctx = ParseCtx()

                first = self._next_item(r, ctx)
                if not isinstance(first, FileHeader):
                    raise MagicMismatch("file does not start with a valid FileHeader")
                yield first

                while r.pos < file_size:
                    item = self._next_item(r, ctx)
                    if item is None:
                        r.skip(1)
                        continue
                    self._update_ctx_after_item(ctx, item)
                    yield item
            finally:
                try:
                    mv.release()
                except Exception:
                    pass
                mm.close()

    def _next_item(self, r: Reader, ctx: ParseCtx) -> Optional[ParsedItem]:
        for p in self.parsers:
            pos_before = r.pos
            item = p.parse(r, ctx)
            if item is not None:
                return item
            if r.pos != pos_before:
                raise ParseError(
                    f"Parser {p.name} moved cursor without returning an item."
                )
        return None

    @staticmethod
    def _update_ctx_after_item(ctx: ParseCtx, item: ParsedItem) -> None:
        """Single source of truth for all contextual state updates."""
        if isinstance(item, RequestMainInfo):
            ctx.seen_rmi.add(item.request_id)
            _observe_ts(ctx, item.ts_leading)
            return

        if isinstance(
            item,
            (RequestHeader, RequestBody, ResponseHeader, ResponseBody, Segment),
        ):
            _observe_ts(ctx, getattr(item, "ts_post", None))

        if isinstance(item, RequestHeader):
            ctx.seen_req_header.add(item.request_id)
            if _is_connect_request_header(item):
                ctx.connect_req.add(item.request_id)
            return

        if isinstance(item, ResponseHeader):
            ctx.seen_resp_header.add(item.request_id)
            if (item.request_id in ctx.connect_req) and _is_connect_200_response_header(
                item
            ):
                ctx.connect_established.add(item.request_id)
                ctx.resp_body_open.discard(item.request_id)
            else:
                ctx.resp_body_open.add(item.request_id)
            return

        if isinstance(item, ResponseBody):
            if item.final:
                ctx.resp_body_open.discard(item.request_id)
            else:
                if item.request_id not in ctx.connect_req:
                    ctx.resp_body_open.add(item.request_id)
            return

        if isinstance(item, Segment):
            if item.kind == "response":
                ctx.resp_body_open.discard(item.request_id)
            return

        if isinstance(item, ConnectTunnelBytes):
            return

        if isinstance(item, ConnectionFrame):
            _observe_ts(ctx, item.ts1)
            _observe_ts(ctx, item.ts2)
            _observe_ts(ctx, item.ts_post1)
            _observe_ts(ctx, item.ts_post2)
            ctx.seen_conn.add(item.connection_id)
            return

        if isinstance(item, TimestampMark):
            _observe_ts(ctx, item.ts)
            return
        # UnknownChunk: no-op


# ==========
# STATUS-LOG Utilities
# ==========

_SEGMENT_TYPES = (
    RequestHeader,
    RequestBody,
    ResponseHeader,
    ResponseBody,
    Segment,
)


def _should_write_status(item: ParsedItem) -> Tuple[bool, Optional[str]]:
    if isinstance(item, _SEGMENT_TYPES):
        reasons: List[str] = []
        status = getattr(item, "status", None)
        missing = getattr(item, "missing_bytes", 0)
        if status is None:
            reasons.append("status=None")
        if missing and missing > 0:
            reasons.append(f"missing_bytes={missing}")
        if reasons:
            return True, ", ".join(reasons)
        return False, None
    if isinstance(item, UnknownChunk):
        return True, "unknown-chunk"
    return False, None


def _format_status_line(item: Union[_SEGMENT_TYPES, UnknownChunk], reason: str) -> str:
    if isinstance(item, _SEGMENT_TYPES):
        pl = 0 if (item.payload is None) else len(item.payload)
        kind = getattr(item, "kind", None) or (
            "response"
            if isinstance(item, (ResponseHeader, ResponseBody))
            else "request"
        )
        subtype = getattr(item, "subtype", "unknown")
        return (
            f"[{reason}] {kind} req={item.request_id} {subtype} status={item.status} "
            f"pos=0x{item.start:X}-0x{item.end:X} len={item.length} "
            f"payload_len={pl} ts_post={item.ts_post}"
        )
    span = item.end - item.start
    hexs, asci = _hex_preview(item.sample)
    long_flag = " long" if span >= 256 else ""
    return (
        f"[{reason}{long_flag}] unknown pos=0x{item.start:X}-0x{item.end:X} "
        f'span={span} head_hex="{hexs}" head_ascii="{asci}"'
    )


def _remove_if_exists(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# ==========
# Metrics harvester (TTFB/transfer/handshake per request_id)
# ==========


def _parse_http_request_line(
    payload: Optional[bytes],
) -> Tuple[Optional[str], Optional[str]]:
    if not payload:
        return None, None
    try:
        head = payload.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    except Exception:
        return None, None
    parts = head.split(" ")
    if len(parts) >= 2 and parts[0].isalpha():
        return parts[0], parts[1]
    return None, None


def _parse_http_status(payload: Optional[bytes]) -> Optional[int]:
    if not payload:
        return None
    try:
        head = payload.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    except Exception:
        return None
    if head.startswith("HTTP/"):
        sp = head.split(" ")
        if len(sp) >= 2 and sp[1].isdigit():
            return int(sp[1])
    return None


@dataclass(slots=True)
class _ReqAgg:
    # linkage
    request_id: int
    connection_id: Optional[int] = None
    # request side
    req_header_ts: Optional[int] = None
    req_method: Optional[str] = None
    req_path: Optional[str] = None
    # response side
    resp_header_ts: Optional[int] = None
    resp_status: Optional[int] = None
    last_resp_body_ts: Optional[int] = None
    resp_final_ts: Optional[int] = None  # prefer final (9)
    # rmi
    rmi_ts_leading: Optional[int] = None


class MetricsHarvester:
    """
    Sammle Metriken je request_id und korreliere zu ConnectionFrames.

    Aufruf:
        mh = MetricsHarvester()
        for item in scanner.scan(...):
            mh.feed(item)
        metrics = mh.finalize()
    """

    def __init__(self) -> None:
        self._by_req: dict[int, _ReqAgg] = {}
        self._conn: dict[int, ConnectionFrame] = {}
        self._rmi_to_conn: dict[int, int] = {}  # request_id -> connection_id

    def _agg(self, request_id: int) -> _ReqAgg:
        a = self._by_req.get(request_id)
        if a is None:
            a = _ReqAgg(request_id=request_id)
            self._by_req[request_id] = a
        return a

    def feed(self, item: ParsedItem) -> None:
        if isinstance(item, RequestMainInfo):
            a = self._agg(item.request_id)
            a.rmi_ts_leading = item.ts_leading
            if item.connection_id is not None:
                self._rmi_to_conn[item.request_id] = item.connection_id
                a.connection_id = a.connection_id or item.connection_id
            return

        if isinstance(item, ConnectionFrame):
            self._conn[item.connection_id] = item
            return

        if isinstance(item, RequestHeader):
            a = self._agg(item.request_id)
            a.req_header_ts = item.ts_post or a.req_header_ts
            method, path = _parse_http_request_line(item.payload)
            a.req_method = a.req_method or method
            a.req_path = a.req_path or path
            return

        if isinstance(item, ResponseHeader):
            a = self._agg(item.request_id)
            a.resp_header_ts = item.ts_post or a.resp_header_ts
            a.resp_status = a.resp_status or _parse_http_status(item.payload)
            return

        if isinstance(item, ResponseBody):
            a = self._agg(item.request_id)
            if item.final:
                a.resp_final_ts = item.ts_post or a.resp_final_ts
            else:
                a.last_resp_body_ts = item.ts_post or a.last_resp_body_ts
            return

    def _choose_transfer_end(self, a: _ReqAgg) -> Optional[int]:
        return a.resp_final_ts or a.last_resp_body_ts

    def finalize(self) -> List[dict]:
        out: List[dict] = []

        for request_id, a in self._by_req.items():
            conn = None
            connection_id = a.connection_id or self._rmi_to_conn.get(request_id)
            if connection_id is not None:
                conn = self._conn.get(connection_id)

            ttfb = None
            if a.req_header_ts is not None and a.resp_header_ts is not None:
                ttfb = a.resp_header_ts - a.req_header_ts

            transfer_ms = None
            resp_end = self._choose_transfer_end(a)
            if a.resp_header_ts is not None and resp_end is not None:
                transfer_ms = resp_end - a.resp_header_ts

            total_ms = None
            if a.req_header_ts is not None and resp_end is not None:
                total_ms = resp_end - a.req_header_ts

            handshake_ms = None
            lifetime_ms = None
            if conn is not None:
                if conn.ts1 and conn.ts2:
                    handshake_ms = conn.ts2 - conn.ts1
                if conn.ts_post2 and conn.ts_post2 > 0:
                    lifetime_ms = conn.ts_post2 - conn.ts1

            out.append(
                {
                    "request_id": request_id,
                    "connection_id": connection_id,
                    "method": a.req_method,
                    "path": a.req_path,
                    "resp_status": a.resp_status,
                    "rmi_ts_leading": a.rmi_ts_leading,
                    "req_header_ts": a.req_header_ts,
                    "resp_header_ts": a.resp_header_ts,
                    "resp_end_ts": resp_end,
                    "metrics": {
                        "ttfb_ms": ttfb,
                        "transfer_ms": transfer_ms,
                        "total_ms": total_ms,
                        "conn_handshake_ms": handshake_ms,
                        "conn_lifetime_ms": lifetime_ms,
                    },
                }
            )
        return out


# ==========
# UnknownChunk coalescing (output layer)
# ==========


def _flush_unknown(pending_unk: Optional[dict], file, sfile) -> Optional[dict]:
    if not pending_unk:
        return None
    start = pending_unk["start"]
    end = pending_unk["end"]
    sample = pending_unk.get("sample", b"")
    length = end - start
    print(
        f"UnknownChunk(start=0x{start:X}, end=0x{end:X}, len={length})",
        file=file,
    )
    hexs, asci = _hex_preview(sample)
    long_flag = " long" if length >= 256 else ""
    print(
        (
            f"[unknown-chunk{long_flag}] pos=0x{start:X}-0x{end:X} "
            f'span={length} head_hex="{hexs}" head_ascii="{asci}"'
        ),
        file=sfile,
    )
    return None


# ==========
# Example CLI entry (+ optional gap report)
# ==========


def main() -> None:
    """
    Minimal demonstration:
    - builds the default scanner,
    - writes a pretty text dump and a status file,
    - coalesces adjacent UnknownChunk items,
    - OPTIONAL: reports unparsed gaps (--report-gaps, --min-gap-bytes N).
    - CLI:
        SESSION_FILE (Pfad zur Session)
        --outdir DIR (Zielordner; default: result_{basename_without_suffix})
    """
    import os

    ap = argparse.ArgumentParser(
        description="Parse HTTP Catcher session file and export reports."
    )
    ap.add_argument(
        "session",
        metavar="SESSION_FILE",
        help="Path to session file to parse",
    )
    ap.add_argument(
        "--outdir",
        help="Output directory. Default: result_{basename_without_suffix}",
        default=None,
    )
    ap.add_argument(
        "--report-gaps",
        action="store_true",
        help="Report unparsed offset ranges and coverage.",
    )
    ap.add_argument(
        "--min-gap-bytes",
        type=int,
        default=1,
        help="Only include gaps >= N bytes in the .gaps.txt report (default 1).",
    )
    args = ap.parse_args()

    session_path = args.session
    base = os.path.basename(session_path)
    stem, _ext = os.path.splitext(base)
    outdir = args.outdir or f"result_{stem}"

    outfile = os.path.join(outdir, f"{stem}.txt")
    statusfile = os.path.join(outdir, f"{stem}.status.txt")
    metrics_outfile = os.path.join(outdir, f"{stem}.metrics.json")
    gaps_outfile = os.path.join(outdir, f"{stem}.gaps.txt")

    os.makedirs(outdir, exist_ok=True)
    _remove_if_exists(outfile)
    _remove_if_exists(statusfile)
    _remove_if_exists(metrics_outfile)
    _remove_if_exists(gaps_outfile)

    scanner = HttpCatcherScanner.default()
    mh = MetricsHarvester()

    # gap aggregation
    unknown_total_all = 0
    unknown_ranges_report: List[Tuple[int, int]] = []

    with (
        open(outfile, "a", encoding="utf-8") as file,
        open(statusfile, "a", encoding="utf-8") as sfile,
    ):
        pending_unk: Optional[dict] = None

        def _flush_and_collect():
            """Flush coalesced unknowns; count all bytes; collect for report if >= threshold."""
            nonlocal pending_unk, unknown_total_all, unknown_ranges_report
            if pending_unk is None:
                return
            start = pending_unk["start"]
            end = pending_unk["end"]
            length = end - start
            unknown_total_all += length
            if length >= max(1, int(args.min_gap_bytes)):
                unknown_ranges_report.append((start, end))
            pending_unk = _flush_unknown(pending_unk, file, sfile)

        for item in scanner.scan(session_path):
            mh.feed(item)

            if isinstance(item, UnknownChunk):
                if pending_unk is None:
                    pending_unk = {
                        "start": item.start,
                        "end": item.end,
                        "sample": item.sample,
                    }
                else:
                    if pending_unk["end"] == item.start:
                        pending_unk["end"] = item.end
                    else:
                        _flush_and_collect()
                        pending_unk = {
                            "start": item.start,
                            "end": item.end,
                            "sample": item.sample,
                        }
                continue

            if pending_unk is not None:
                _flush_and_collect()

            print(item, sep="\n\n", file=file)

            write, reason = _should_write_status(item)
            if write and isinstance(item, (_SEGMENT_TYPES + (UnknownChunk,))):
                print(_format_status_line(item, reason), file=sfile)

        if pending_unk is not None:
            _flush_and_collect()

    # optional coverage / gap report
    if args.report_gaps:
        size = os.path.getsize(session_path)
        covered = size - unknown_total_all
        coverage = (covered / size * 100.0) if size > 0 else 0.0
        print(f"Coverage: {covered}/{size} bytes ({coverage:.2f}%)")

        with open(gaps_outfile, "w", encoding="utf-8") as gf:
            for s, e in unknown_ranges_report:
                print(f"0x{s:08X}-0x{e:08X} ({e - s} bytes)", file=gf)
        print(f"Wrote gaps: {gaps_outfile}")

    metrics = mh.finalize()
    with open(metrics_outfile, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"Wrote metrics: {metrics_outfile}")


if __name__ == "__main__":
    main()
