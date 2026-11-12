"""Fixed-origin, bounded HTTP transport for the workbench's closed read-only tools."""

from __future__ import annotations

import http.client
import io
import json
import math
import re
import socket
import ssl
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast
from urllib.parse import quote, urlsplit

from mission.models import JSONObject

if TYPE_CHECKING:
    from http.client import _DataType

    from _typeshed import WriteableBuffer

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_HTTP_METADATA_BYTES = 64 * 1024
MAX_HTTP_METADATA_LINE_BYTES = 8192
MAX_HTTP_TRAILER_FIELDS = 100
_HTTP_TOKEN = rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+"


class WorkbenchError(ValueError):
    """Report unavailable evidence, invalid input or a refused query without retrying."""


def _remaining(deadline: float) -> float:
    """Use the original monotonic deadline without renewing it after partial progress."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("original HTTP deadline expired")
    return remaining


class _DeadlineReader(io.RawIOBase):
    """Apply the original remaining budget before each actual plain or TLS socket read."""

    def __init__(self, stream_socket: socket.socket, deadline: float) -> None:
        """Own a socket file reference so HTTP/1.0 connection close cannot invalidate body reads."""
        super().__init__()
        self._socket = stream_socket
        self._deadline = deadline
        self._source = cast(io.RawIOBase, stream_socket.makefile("rb", buffering=0))

    def readable(self) -> bool:
        """Allow buffered header, chunk framing and body reads through this same deadline."""
        return True

    def readinto(self, buffer: WriteableBuffer) -> int | None:
        """Refuse a slow drip even when each individual receive arrives before its idle timeout."""
        self._socket.settimeout(_remaining(self._deadline))
        count = self._source.readinto(buffer)
        _remaining(self._deadline)
        return count

    def close(self) -> None:
        """Release the original socket file reference on every response close or parse failure."""
        try:
            self._source.close()
        finally:
            super().close()


class _FramedReader(io.BufferedReader):
    """Bound status/header/chunk/trailer metadata without counting payload as metadata."""

    def __init__(self, source: _DeadlineReader) -> None:
        """Keep the same original deadline-aware source through all parser reads."""
        super().__init__(source)
        self.metadata_bytes = 0

    def readline(self, size: int | None = -1) -> bytes:
        """Require complete CRLF lines and one cumulative budget for all HTTP metadata."""
        limit = MAX_HTTP_METADATA_LINE_BYTES + 1
        line = super().readline(min(size, limit) if size is not None and size >= 0 else limit)
        self.metadata_bytes += len(line)
        if (
            len(line) > MAX_HTTP_METADATA_LINE_BYTES
            or self.metadata_bytes > MAX_HTTP_METADATA_BYTES
            or not line.endswith(b"\r\n")
            or line.startswith((b" ", b"\t"))
        ):
            raise WorkbenchError("HTTP 响应不完整或分帧元数据无效。")
        return line


def _response_class(deadline: float) -> type[http.client.HTTPResponse]:
    """Bind a per-request response reader without threads, signals or shared timeout state."""

    class DeadlineResponse(http.client.HTTPResponse):
        """Use the standard HTTP parser with a deadline-aware buffered socket file."""

        def __init__(
            self,
            stream_socket: socket.socket,
            debuglevel: int = 0,
            method: str | None = None,
            url: str | None = None,
        ) -> None:
            """Replace the standard file before the parser consumes any status or header bytes."""
            super().__init__(stream_socket, debuglevel, method, url)
            self._request_method = method
            if self.fp is not None:
                self.fp.close()
            self.fp = _FramedReader(_DeadlineReader(stream_socket, deadline))

        def begin(self) -> None:
            """Refuse ambiguous framing rather than accepting the parser's first header value."""
            super().begin()
            assert self.headers is not None
            if self.headers.defects:
                raise WorkbenchError("HTTP 响应头无效。")
            lengths = self.headers.get_all("Content-Length", [])
            transfers = self.headers.get_all("Transfer-Encoding", [])
            if transfers:
                if lengths or len(transfers) != 1 or transfers[0].lower().strip(" \t") != "chunked":
                    raise WorkbenchError("HTTP 响应分帧字段冲突或不受支持。")
                self.chunked, self.chunk_left = True, None
            if lengths:
                values = [part.strip(" \t") for value in lengths for part in value.split(",")]
                if any(re.fullmatch(r"[0-9]+", value) is None for value in values):
                    raise WorkbenchError("HTTP Content-Length 无效。")
                normalized = {value.lstrip("0") or "0" for value in values}
                if len(normalized) != 1:
                    raise WorkbenchError("HTTP Content-Length 冲突。")
                length = normalized.pop()
                if len(length) > len(str(MAX_RESPONSE_BYTES)) or int(length) > MAX_RESPONSE_BYTES:
                    raise WorkbenchError("响应超过 2 MiB，未生成部分统计。")
                if self._request_method != "HEAD" and not (
                    self.status in {204, 304} or 100 <= self.status < 200
                ):
                    self.length = int(length)

        def _read_next_chunk_size(self) -> int:
            """Accept a complete bounded hexadecimal size and validate any extension syntax."""
            assert self.fp is not None
            line = self.fp.readline()
            size, separator, extensions = line[:-2].partition(b";")
            if (
                re.fullmatch(rb"[0-9A-Fa-f]+[ \t]*" if separator else rb"[0-9A-Fa-f]+", size)
                is None
            ):
                raise WorkbenchError("HTTP chunk 大小无效。")
            if separator:
                # Extensions do not carry application facts, but their quoting must be closed.
                quoted = rb'"(?:[\t !#-\[\]-~\x80-\xff]|\\[\t -~\x80-\xff])*"'
                extension = (
                    _HTTP_TOKEN + rb"(?:[ \t]*=[ \t]*(?:" + _HTTP_TOKEN + b"|" + quoted + rb"))?"
                )
                if (
                    re.fullmatch(
                        rb"[ \t]*" + extension + rb"(?:[ \t]*;[ \t]*" + extension + rb")*[ \t]*",
                        extensions,
                    )
                    is None
                ):
                    raise WorkbenchError("HTTP chunk 扩展无效。")
            return int(size, 16)

        def _read_and_discard_trailer(self) -> None:
            """Require the terminal CRLF and bounded, syntactically complete trailer fields."""
            assert self.fp is not None
            for _ in range(MAX_HTTP_TRAILER_FIELDS + 1):
                line = self.fp.readline()
                if line == b"\r\n":
                    return
                name, separator, value = line[:-2].partition(b":")
                if (
                    not separator
                    or re.fullmatch(_HTTP_TOKEN, name) is None
                    or name.lower() in {b"content-length", b"transfer-encoding", b"trailer"}
                    or any(byte < 32 and byte != 9 or byte == 127 for byte in value)
                ):
                    raise WorkbenchError("HTTP trailer 无效。")
            raise WorkbenchError("HTTP trailer 超出字段上限。")

        def _get_chunk_left(self) -> int | None:
            """Validate each exact chunk terminator before advancing the standard body reader."""
            chunk_left = self.chunk_left
            if not chunk_left:
                assert self.fp is not None
                if chunk_left is not None and self.fp.read(2) != b"\r\n":
                    raise WorkbenchError("HTTP chunk 终止符无效。")
                chunk_left = self._read_next_chunk_size()
                if chunk_left == 0:
                    self._read_and_discard_trailer()
                    self.close()
                    chunk_left = None
                self.chunk_left = chunk_left
            return chunk_left

    return DeadlineResponse


def _connection_classes(
    deadline: float,
) -> tuple[type[http.client.HTTPConnection], type[http.client.HTTPSConnection]]:
    """Keep address attempts, TLS and separate request sends inside one original budget."""

    def connect_socket(
        address: tuple[str, int],
        timeout: float | None,
        source_address: tuple[str, int] | None,
    ) -> socket.socket:
        """Resolve once, then bound every actual TCP attempt by the original remaining time."""
        last_error: OSError | None = None
        for family, kind, protocol, _, target in socket.getaddrinfo(
            *address, type=socket.SOCK_STREAM
        ):
            stream_socket: socket.socket | None = None
            try:
                _remaining(deadline)
                stream_socket = socket.socket(family, kind, protocol)
                stream_socket.settimeout(_remaining(deadline))
                if source_address is not None:
                    stream_socket.bind(source_address)
                stream_socket.connect(target)
                # HTTPSConnection wraps this exact socket next; TLS inherits only what remains.
                stream_socket.settimeout(_remaining(deadline))
                return stream_socket
            except OSError as error:
                last_error = error
                if stream_socket is not None:
                    stream_socket.close()
        if last_error is not None:
            raise last_error
        raise OSError("address resolution returned no stream address")

    class DeadlineConnection(http.client.HTTPConnection):
        """Retain the standard HTTP implementation with request-local socket budgets."""

        def connect(self) -> None:
            """Use the original remaining time for TCP and the inherited TLS handshake."""
            self._create_connection = connect_socket
            super().connect()
            _remaining(deadline)

        def send(self, data: _DataType | str) -> None:
            """Bound each headers/body send generated from the caller's original bytes."""
            if self.sock is None:
                if not self.auto_open:
                    raise http.client.NotConnected()
                self.connect()
            self.sock.settimeout(_remaining(deadline))
            super().send(data)
            _remaining(deadline)

    class DeadlineHTTPSConnection(DeadlineConnection, http.client.HTTPSConnection):
        """Preserve HTTPSConnection's context, hostname checks, ALPN and TLS wrapping."""

    return DeadlineConnection, DeadlineHTTPSConnection


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous repeated JSON keys at every nesting level."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WorkbenchError("JSON 包含重复字段。")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    """Refuse non-JSON NaN and infinity spellings before they become evidence."""
    raise WorkbenchError("JSON 包含非有限数值。")


def _finite_float(value: str) -> float:
    """Reject finite-looking JSON exponents that overflow the host numeric representation."""
    result = float(value)
    if not math.isfinite(result):
        raise WorkbenchError("JSON 数值超出有限范围。")
    return result


def decode_object(raw: str | bytes | bytearray) -> JSONObject:
    """Decode an unambiguous finite JSON object, bounding failure to a query error."""
    try:
        decoded: object = json.loads(
            raw,
            object_pairs_hook=_unique_fields,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise WorkbenchError("响应不是有效、无歧义的 JSON。") from error
    if not isinstance(decoded, dict):
        raise WorkbenchError("响应必须为 JSON 对象。")
    return cast(JSONObject, decoded)


def identifier(value: str) -> str:
    """Accept a bounded literal identity, refusing path traversal and control characters."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 256
        or value in {".", ".."}
        or any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise WorkbenchError("对象 ID 必须为 1–256 字符的有效文本。")
    return value


def request_json(
    url: str,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
    context: ssl.SSLContext | None = None,
    accepted_statuses: tuple[int, ...] = (200,),
) -> JSONObject:
    """Read one bounded response without redirects, retries or response-body error disclosure.

    Socket operations consume the original monotonic deadline. DNS resolution remains subject
    to the host resolver; this is not a real-time guarantee. Every connection closes on exit.
    """
    if not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise WorkbenchError("请求超时必须在 0–600 秒之间。")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WorkbenchError("无效的 HTTP(S) 地址。")
    deadline = time.monotonic() + timeout
    plain_connection, tls_connection = _connection_classes(deadline)
    connection: http.client.HTTPConnection
    if parsed.scheme == "https":
        connection = tls_connection(parsed.hostname, parsed.port, timeout=timeout, context=context)
    else:
        connection = plain_connection(parsed.hostname, parsed.port, timeout=timeout)
    connection.response_class = _response_class(deadline)
    response: http.client.HTTPResponse | None = None
    try:
        connection.connect()
        if connection.sock is None:
            raise WorkbenchError("连接不可用。")
        connection.sock.settimeout(_remaining(deadline))
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request(method, path, body=body, headers=dict(headers))
        connection.sock.settimeout(_remaining(deadline))
        socket = connection.sock
        response = connection.getresponse()
        if response.status not in accepted_statuses:
            raise WorkbenchError(f"查询未取得有效结果：HTTP {response.status}。")
        raw = bytearray()
        while not response.isclosed():
            socket.settimeout(_remaining(deadline))
            chunk = response.read1(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - len(raw)))
            raw.extend(chunk)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise WorkbenchError("响应超过 2 MiB，未生成部分统计。")
            if not chunk:
                break
        if response.length not in {None, 0}:
            raise WorkbenchError("响应不完整，当前状态未知。")
        decoded = decode_object(raw)
        _remaining(deadline)
        return decoded
    except (OSError, http.client.HTTPException) as error:
        raise WorkbenchError("连接失败或超时，当前状态未知。") from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise WorkbenchError("响应不是有效的 UTF-8 JSON。") from error
    finally:
        if response is not None:
            response.close()
        connection.close()


class ReadOnlyController:
    """Expose only inventory and exact-Mission GETs under one operator connection."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout: float = 5,
        tenant: str | None = None,
        ca: Path | None = None,
        certificate: Path | None = None,
        key: Path | None = None,
    ) -> None:
        """Freeze a validated origin and require complete mTLS for remote Controllers."""
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise WorkbenchError("Controller 必须是固定 HTTP(S) origin，不含路径或凭据。")
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise WorkbenchError("Controller 超时必须在 0–60 秒之间。")
        tls = (ca, certificate, key)
        if any(tls) and not all(tls):
            raise WorkbenchError("mTLS 需要同时提供 CA、客户端证书和私钥。")
        if any(tls) and parsed.scheme != "https":
            raise WorkbenchError("mTLS 必须使用 HTTPS。")
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} and not (
            parsed.scheme == "https" and all(tls)
        ):
            raise WorkbenchError("远程 Controller 需要 HTTPS 和完整 mTLS 配置。")
        self.context: ssl.SSLContext | None = None
        if ca is not None and certificate is not None and key is not None:
            self.context = ssl.create_default_context(cafile=str(ca))
            self.context.load_cert_chain(certificate, key)
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.tenant = identifier(tenant) if tenant is not None else None

    def _get(self, path: str) -> JSONObject:
        """Send one derived GET; server authorization remains the authority for visibility."""
        headers = {"Accept": "application/json"}
        if self.tenant is not None:
            headers["X-RoboGuide-Tenant"] = self.tenant
        return request_json(self.endpoint + path, "GET", headers, None, self.timeout, self.context)

    def inventory(self) -> JSONObject:
        """Read the current identity-visible inventory without selecting any executor."""
        return self._get("/v1/inventory")

    def mission(self, mission_id: str) -> JSONObject:
        """Read an encoded literal Mission identity without accepting arbitrary route suffixes."""
        return self._get("/v1/missions/" + quote(identifier(mission_id), safe=""))

    def missions(self) -> JSONObject:
        """Discover identity-visible Mission records using the existing scoped State projection."""
        return self._get("/v1/state/records?object_type=mission")

    def mission_bindings(self, group_id: str) -> JSONObject:
        """Read exact Group assignments as facts, never as new scheduling authority."""
        return self._get(
            "/v1/state/records?object_type=execution_group&object_id="
            + quote(identifier(group_id), safe="")
        )

    def mission_recovery(self, mission_id: str) -> JSONObject:
        """Read the original recovery assessment without triggering recovery or retry."""
        return self._get("/v1/missions/" + quote(identifier(mission_id), safe="") + "/recovery")
