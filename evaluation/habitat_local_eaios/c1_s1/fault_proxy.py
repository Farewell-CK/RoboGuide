"""C1-S1 fault-injection TCP proxy between roboguide-node and the Habitat bridge.

Forwards every HTTP request byte-for-byte, except when an armed fault rule
matches a status query.  Supported fault kinds (one armed rule per run):

- ``fail-status:K``      answer the first K status POSTs with HTTP 500
- ``timeout-status:K``   swallow the first K status POSTs (node HTTP timeout)
- ``malformed-status:K`` answer the first K status POSTs with a malformed 200
- ``refuse-window:S``    refuse every connection for S seconds from startup

This is evaluation-only infrastructure; it owns no RoboGuide authority.
"""

from __future__ import annotations

import argparse
import socket
import threading
import time


class FaultProxy:
    """One threaded TCP proxy with deterministic fault rules."""

    def __init__(
        self,
        listen_port: int,
        target_port: int,
        kind: str,
        parameter: int,
    ) -> None:
        """Store the forwarding target and the single armed fault rule."""
        self._listen_port = listen_port
        self._target_port = target_port
        self._kind = kind
        self._parameter = parameter
        self._status_seen = 0
        self._counter_lock = threading.Lock()
        self._started_at = time.monotonic()
        self.log: list[str] = []

    def serve(self) -> None:
        """Accept connections forever, applying the armed fault rule."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self._listen_port))
        listener.listen(16)
        while True:
            client, _ = listener.accept()
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _note(self, message: str) -> None:
        """Record one fault-proxy event for the evidence log."""
        line = f"{time.monotonic() - self._started_at:8.2f}s {message}"
        self.log.append(line)
        print(line, flush=True)

    def _handle(self, client: socket.socket) -> None:
        """Apply refusal faults, then classify and relay or sabotage one request."""
        if self._kind == "refuse-window":
            if time.monotonic() - self._started_at < self._parameter:
                self._note("refuse-window: closing inbound connection")
                client.close()
                return
        try:
            request = self._read_request(client)
        except (OSError, ValueError):
            client.close()
            return
        head = request.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        is_status = "POST /v1/executions/status" in head
        if is_status:
            with self._counter_lock:
                self._status_seen += 1
                ordinal = self._status_seen
            if self._kind in {"fail-status", "timeout-status", "malformed-status"} and (
                ordinal <= self._parameter
            ):
                self._note(f"{self._kind} hit on status #{ordinal}")
                if self._kind == "fail-status":
                    client.sendall(
                        b"HTTP/1.1 500 Internal Server Error\r\n"
                        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                elif self._kind == "malformed-status":
                    client.sendall(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: 15\r\nConnection: close\r\n\r\n"
                        b'{"broken": tru'
                    )
                else:
                    time.sleep(self._parameter + 8)
                client.close()
                return
        self._note(f"forward: {head.split(' ', 1)[0] if ' ' in head else head}")
        self._relay(client, request)

    def _read_request(self, client: socket.socket) -> bytes:
        """Read one complete HTTP request including its content-length body."""
        client.settimeout(10.0)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                raise ValueError("peer closed before header end")
            data += chunk
        header_end = data.index(b"\r\n\r\n") + 4
        length = 0
        for line in data[:header_end].split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        body = data[header_end:]
        while len(body) < length:
            chunk = client.recv(4096)
            if not chunk:
                raise ValueError("peer closed before body end")
            body += chunk
        return data[:header_end] + body

    def _relay(self, client: socket.socket, request: bytes) -> None:
        """Open the target connection and pipe both directions until either closes."""
        target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target.connect(("127.0.0.1", self._target_port))
        target.sendall(request)

        def pump(source: socket.socket, sink: socket.socket) -> None:
            """Copy bytes until the source closes or errors."""
            try:
                while True:
                    chunk = source.recv(65536)
                    if not chunk:
                        break
                    sink.sendall(chunk)
            except OSError:
                pass
            finally:
                try:
                    sink.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        to_target = threading.Thread(target=pump, args=(client, target), daemon=True)
        to_client = threading.Thread(target=pump, args=(target, client), daemon=True)
        to_target.start()
        to_client.start()
        to_target.join()
        to_client.join()
        client.close()
        target.close()


def main() -> None:
    """Parse arguments and serve the armed fault proxy."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument(
        "--fault",
        required=True,
        help="fault rule: fail-status:K | timeout-status:K | malformed-status:K | refuse-window:S",
    )
    arguments = parser.parse_args()
    kind, _, raw = arguments.fault.partition(":")
    parameter = float(raw) if raw else 0
    if kind == "refuse-window":
        parameter_value: int = int(parameter)
    else:
        parameter_value = int(parameter)
    proxy = FaultProxy(arguments.listen_port, arguments.target_port, kind, parameter_value)
    thread = threading.Thread(target=proxy.serve, daemon=True)
    thread.start()
    print(
        f"fault proxy {kind}:{parameter_value} listening on "
        f"127.0.0.1:{arguments.listen_port} -> {arguments.target_port}",
        flush=True,
    )
    try:
        while thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for line in proxy.log:
            print(line, flush=True)


if __name__ == "__main__":
    main()
