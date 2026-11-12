"""Actual HTTP framing failures and address-family fallback must retain unavailable evidence."""

from __future__ import annotations

import errno
import json
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from roboguide_workbench import client
from roboguide_workbench.client import WorkbenchError, request_json
from test_workbench import inventory
from test_workbench_deadline import response_server


@contextmanager
def raw_response(raw: bytes) -> Iterator[tuple[str, list[str]]]:
    """Send the exact original raw response and join the actual fixture handler on exit."""

    def send(connection: socket.socket, stopped: threading.Event) -> None:
        """Deliver framing bytes unchanged instead of letting a fixture normalize headers."""
        connection.sendall(raw)

    with response_server(send) as served:
        yield served


def response(headers: bytes, body: bytes, version: bytes = b"1.1") -> bytes:
    """Compose original response bytes without supplying implicit framing or termination."""
    return b"HTTP/" + version + b" 200 OK\r\n" + headers + b"Connection: close\r\n\r\n" + body


@pytest.mark.parametrize(
    "raw",
    [
        response(b"Content-Length: 2\r\nContent-Length: 102\r\n", b"{}"),
        response(b"Content-Length: 2, 102\r\n", b"{}"),
        response(b"Content-Length: +2\r\n", b"{}"),
        response(b"Content-Length: -2\r\n", b"{}"),
        response(b"Content-Length: invalid\r\n", b"{}"),
        response(b"Content-Length: 2\xa0\r\n", b"{}"),
        response(b"Transfer-Encoding:\r\n chunked\r\n", b"2\r\n{}\r\n0\r\n\r\n"),
        response(b"Content-Length: 2\r\nTransfer-Encoding: chunked\r\n", b"2\r\n{}\r\n0\r\n\r\n"),
        response(
            b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n", b"2\r\n{}\r\n0\r\n\r\n"
        ),
        response(b"Transfer-Encoding: gzip, chunked\r\n", b"2\r\n{}\r\n0\r\n\r\n"),
        b"HTTP/1.1 200 OK\nContent-Length: 2\n\n{}",
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n",
        response(b"Transfer-Encoding: chunked\r\n", b"2\r\n{}XX0\r\n\r\n"),
        response(b"Transfer-Encoding: chunked\r\n", b"2\r\n{}\r\n0\r\n"),
        response(b"Transfer-Encoding: chunked\r\n", b"2\r\n{}\r\n"),
        response(b"Transfer-Encoding: chunked\r\n", b"+2\r\n{}\r\n0\r\n\r\n"),
        response(b"Transfer-Encoding: chunked\r\n", b"0x2\r\n{}\r\n0\r\n\r\n"),
        response(b"Transfer-Encoding: chunked\r\n", b'2;foo="unclosed\r\n{}\r\n0\r\n\r\n'),
        response(b"Transfer-Encoding: chunked\r\n", b"2\r\n{}\r\n0\r\nContent-Length: 2\r\n\r\n"),
        response(b"Transfer-Encoding: chunked\r\n", b"2\r\n{}\r\n0\r\nnot-a-field\r\n\r\n"),
        response(b"Transfer-Encoding: chunked\r\n", b"2\r\n{}\r\n0\r\nX-Final: no\r\n"),
    ],
    ids=[
        "conflicting-lengths",
        "conflicting-combined-lengths",
        "signed-length",
        "negative-length",
        "invalid-length",
        "non-http-length-whitespace",
        "folded-transfer",
        "length-and-transfer",
        "duplicate-transfer",
        "unsupported-transfer",
        "bare-lf",
        "incomplete-header-block",
        "wrong-chunk-end",
        "missing-final-crlf",
        "missing-final-chunk",
        "signed-chunk-size",
        "prefixed-chunk-size",
        "unclosed-extension",
        "framing-trailer",
        "invalid-trailer",
        "incomplete-trailer-block",
    ],
)
def test_malformed_framing_is_unavailable_without_post_replay(raw: bytes) -> None:
    """Neither a valid JSON prefix nor a sent command can make malformed framing complete."""
    with raw_response(raw) as (endpoint, calls):
        with pytest.raises(WorkbenchError):
            request_json(endpoint, "POST", {"Content-Type": "application/json"}, b"{}", 1)
        assert calls == ["POST"]


@pytest.mark.parametrize(
    "raw",
    [
        response(b"Content-Length: 2\r\n", b"{}", b"1.0"),
        response(b"Content-Length: 2\r\n", b"{}"),
        response(b"Content-Length: 2\r\nContent-Length: 002\r\n", b"{}"),
        response(b"Content-Length: 002, 2\r\n", b"{}"),
        response(b"", b"{}"),
        response(b"Transfer-Encoding: chunked\r\n", b"1\r\n{\r\n1\r\n}\r\n0\r\n\r\n"),
        response(b"Transfer-Encoding: Chunked \r\n", b"2\r\n{}\r\n0\r\nX-Final: yes\r\n\r\n"),
        response(
            b"Transfer-Encoding: chunked\r\n",
            b'2 ; foo = "quoted\\";value"; flag\r\n{}\r\n0;end=yes\r\n\r\n',
        ),
    ],
    ids=[
        "http10",
        "http11",
        "same-length-fields",
        "same-length-list",
        "eof-body",
        "multiple-chunks",
        "trailer-and-ows",
        "extensions",
    ],
)
def test_valid_original_framing_remains_supported(raw: bytes) -> None:
    """Strict framing retains normal fixed, chunked and connection-close responses."""
    with raw_response(raw) as (endpoint, calls):
        assert request_json(endpoint, "GET", {}, None, 1) == {}
        assert calls == ["GET"]


@pytest.mark.parametrize("case", ["line", "total", "trailer-fields", "chunk-metadata"])
def test_chunk_and_header_metadata_have_independent_byte_and_line_bounds(case: str) -> None:
    """Large extensions or ignored trailers cannot consume an unbounded metadata stream."""
    if case == "line":
        raw = response(b"X-Large: " + b"a" * client.MAX_HTTP_METADATA_LINE_BYTES + b"\r\n", b"{}")
    elif case == "total":
        raw = response(
            b"Transfer-Encoding: chunked\r\n",
            b"2\r\n{}\r\n0\r\n" + (b"X-Large: " + b"a" * 7000 + b"\r\n") * 10 + b"\r\n",
        )
    elif case == "trailer-fields":
        raw = response(
            b"Transfer-Encoding: chunked\r\n",
            b"2\r\n{}\r\n0\r\n"
            + b"X-Extra: yes\r\n" * (client.MAX_HTTP_TRAILER_FIELDS + 1)
            + b"\r\n",
        )
    else:
        chunk = b"1;x=" + b"a" * 7000 + b"\r\n \r\n"
        raw = response(b"Transfer-Encoding: chunked\r\n", chunk * 10 + b"2\r\n{}\r\n0\r\n\r\n")
    with raw_response(raw) as (endpoint, calls):
        with pytest.raises(WorkbenchError):
            request_json(endpoint, "GET", {}, None, 1)
        assert calls == ["GET"]


def test_payload_does_not_consume_the_metadata_budget() -> None:
    """A valid body larger than the metadata limit remains usable within its original 2 MiB cap."""
    body = json.dumps({"value": "x" * (client.MAX_HTTP_METADATA_BYTES + 1)}).encode()
    raw = response(f"Content-Length: {len(body)}\r\n".encode(), body)
    with raw_response(raw) as (endpoint, _):
        assert request_json(endpoint, "GET", {}, None, 1) == json.loads(body)


@pytest.mark.parametrize("case", ["conflicting-lengths", "bad-chunk-end", "missing-trailer-end"])
def test_installed_cli_refuses_valid_inventory_in_invalid_http_framing(case: str) -> None:
    """Actual CLI refuses false successful zero-node facts from malformed original HTTP bytes."""
    body = json.dumps(inventory()).encode()
    if case == "conflicting-lengths":
        raw = response(
            f"Content-Length: {len(body)}\r\nContent-Length: {len(body) + 100}\r\n".encode(), body
        )
    else:
        end = b"XX0\r\n\r\n" if case == "bad-chunk-end" else b"\r\n0\r\n"
        raw = response(
            b"Transfer-Encoding: chunked\r\n", f"{len(body):x}\r\n".encode() + body + end
        )
    with raw_response(raw) as (endpoint, calls):
        result = subprocess.run(
            [str(Path(sys.executable).parent / "roboguide"), "nodes", "--controller", endpoint],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    assert result.returncode == 2, result.stdout
    assert "0 个" not in result.stdout and "查询失败" in result.stderr
    assert calls == ["GET"]


def test_unsupported_first_address_family_falls_back_to_actual_ipv4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed socket constructor cannot prevent a later original resolver candidate working."""
    raw = response(b"Content-Length: 2\r\n", b"{}")
    actual_socket = socket.socket
    attempts: list[int] = []
    with raw_response(raw) as (endpoint, calls):
        port = int(endpoint.rsplit(":", 1)[1])

        def lookup(*args: object, **kwargs: object) -> list[Any]:
            """Return an unavailable IPv6 candidate before the fixture's genuine IPv4 listener."""
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
            ]

        def create(
            family: int = socket.AF_INET,
            type: int = socket.SOCK_STREAM,
            proto: int = 0,
            fileno: int | None = None,
        ) -> socket.socket:
            """Fault only unsupported socket creation; actual connect and HTTP remain unmodified."""
            attempts.append(family)
            if family == socket.AF_INET6:
                raise OSError(errno.EAFNOSUPPORT, "address family unavailable")
            return actual_socket(family, type, proto, fileno)

        monkeypatch.setattr(socket, "getaddrinfo", lookup)
        monkeypatch.setattr(socket, "socket", create)
        assert request_json(endpoint, "GET", {}, None, 1) == {}
        assert calls == ["GET"]
        assert attempts[:2] == [socket.AF_INET6, socket.AF_INET]
