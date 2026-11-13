"""Exercise an installed RG chat agent through a real PTY and recorded HTTP boundaries.

The Controller is explicitly synthetic. ``--real-model`` forwards native Responses requests
through a local recording relay to the configured Provider; no model output is substituted.
This gate creates no Nodes, Mission execution, physical observations or field deployment.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pty
import re
import select
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
MAX_HTTP = 2 * 1024 * 1024


def save(path: Path, value: Any) -> None:
    """Retain a private, reviewable JSON receipt without including HTTP credentials."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def digest(path: Path) -> str:
    """Return the exact artifact hash used in this software acceptance run."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(mission: str, channel: str, value: dict[str, Any]) -> dict[str, Any]:
    """Construct the canonical source attribution emitted by the current State facade."""
    desired = channel == "accepted-plan"
    return {
        "object": {"class": "roboguide", "object_type": "mission", "object_id": mission},
        "semantic": "desired" if desired else "derived",
        "source": "roboguide:mission-orchestrator"
        if desired
        else "roboguide:runtime-orchestration",
        "channel_id": channel,
        "value": value,
        "stale": None,
    }


def inventory() -> dict[str, Any]:
    """Return two software fixtures with distinct advertised capability contracts."""
    observed = int(time.time() * 1000)
    return {
        "schema_version": "roboguide.inventory/v0.1",
        "observed_at_ms": observed,
        "nodes": [
            {
                "node_id": name,
                "reported_health": "Online",
                "source_observed_at_ms": observed - 100,
                "received_at_ms": observed - 50,
                "liveness": "Reachable",
                "liveness_observed_at_ms": observed - 100,
                "capabilities": [{"kind": "compute", "available": True}],
                "contracts": [contract],
                "resources": [{"resource_id": "cpu", "kind": "compute", "capacity": 4}],
            }
            for name, contract in (
                ("node-alpha", "compute.infer@v1"),
                ("node-beta", "spatial.map.build@v0"),
            )
        ],
    }


def mission_state() -> dict[str, Any]:
    """Supply long historical objectives and one active canonical Mission independently of Tasks."""
    records: list[dict[str, Any]] = []
    for index in range(180):
        name = f"mission-history-{index:03d}" if index else "mission-running"
        records.extend(
            [
                record(name, "accepted-plan", {"objective": "软件验收长目标。" * 150}),
                record(
                    name,
                    "mission-lifecycle",
                    {"lifecycle": "Running" if index == 0 else "Completed"},
                ),
            ]
        )
    records.append(
        {
            "object": {"class": "roboguide", "object_type": "task", "object_id": "old-task"},
            "semantic": "derived",
            "source": "roboguide:runtime-orchestration",
            "channel_id": "task-lifecycle",
            "value": {"lifecycle": "Active"},
            "stale": None,
        }
    )
    return {
        "schema": "roboguide.state-query/v0.1",
        "observed_at_ms": int(time.time() * 1000),
        "records": records,
    }


@dataclass
class Trace:
    """Own HTTP observations and explicit fault controls for this one isolated run."""

    reads: list[dict[str, Any]] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    provider: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    delay_next_inventory: bool = False
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)


@contextmanager
def serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    """Start and completely drain an owned loopback HTTP listener."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("owned HTTP server did not drain")


class JsonHandler(BaseHTTPRequestHandler):
    """Share finite JSON framing while suppressing noisy fixture access logs."""

    def log_message(self, format: str, *args: Any) -> None:
        """Keep access logging in the explicit credential-free request trace."""

    def send_json(self, status: int, value: Any) -> None:
        """Send one finite JSON response and close the owned request connection."""
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


def controller_handler(trace: Trace) -> type[JsonHandler]:
    """Expose only the production-shaped read facade and record every attempted write."""

    class Controller(JsonHandler):
        """Serve current fixture facts, including one deliberate unavailable exact Mission."""

        def do_GET(self) -> None:
            """Observe the actual GET and consume only explicitly armed bounded delays."""
            with trace.lock:
                trace.reads.append(
                    {
                        "path": self.path,
                        "received_at": time.time(),
                        "tenant": self.headers.get("X-RoboGuide-Tenant"),
                    }
                )
                delay = self.path == "/v1/inventory" and trace.delay_next_inventory
                if delay:
                    trace.delay_next_inventory = False
            if self.headers.get("X-RoboGuide-Tenant") != "fixture":
                self.send_json(403, {"error": "wrong software fixture tenant"})
                return
            if delay:
                trace.entered.set()
                if not trace.release.wait(8):
                    self.send_json(503, {"error": "fixture delay was not released"})
                    return
            if self.path == "/v1/inventory":
                self.send_json(200, inventory())
            elif self.path.startswith("/v1/state/records?"):
                self.send_json(200, mission_state())
            elif self.path == "/v1/missions/mission-unavailable":
                self.send_json(503, {"error": "deliberate unavailable fixture"})
            elif self.path == "/v1/missions/mission-running":
                self.send_json(
                    200,
                    {
                        "mission_id": "mission-running",
                        "group_id": "group-running",
                        "status": "Running",
                        "tasks": [],
                    },
                )
            elif self.path == "/readyz":
                self.send_json(200, {"status": "ok"})
            else:
                self.send_json(404, {"error": "no fixture at this route"})

        def do_POST(self) -> None:
            """Reject and retain every attempted business write instead of inventing execution."""
            trace.writes.append(self.path)
            self.send_json(405, {"error": "no effects permitted by this software gate"})

    return Controller


def message_text(content: Any) -> str:
    """Read native Responses input text without depending on the old Query JSON protocol."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def last_question(payload: dict[str, Any]) -> str:
    """Select the current user item from a role-aware Responses input sequence."""
    supplied = payload.get("input")
    if not isinstance(supplied, list):
        raise ValueError("agent loop must use native role-aware Responses input items")
    for item in reversed(supplied):
        if isinstance(item, dict) and item.get("role") == "user":
            return message_text(item.get("content"))
    raise ValueError("Responses request is missing a user message")


def final_message(text: str) -> dict[str, Any]:
    """Return a normal Assistant output item without forced answer/citations JSON."""
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def fixture_response(payload: dict[str, Any], number: int) -> dict[str, Any]:
    """Implement scripted native function calls for an offline transport and UX gate only."""
    question = last_question(payload)
    supplied = payload["input"]
    assert isinstance(supplied, list)
    completed = bool(
        supplied
        and isinstance(supplied[-1], dict)
        and supplied[-1].get("type") == "function_call_output"
    )
    if completed:
        if "unavailable" in question:
            return final_message("这条任务的当前状态暂时无法读取，不能判断它是否还在运行。")
        if "任务" in question:
            return final_message("当前有 1 个 Mission 正在运行，名称是 mission-running。")
        if "第二个" in question:
            return final_message("第二个节点 node-beta 提供建图合同。")
        return final_message("当前可见两个节点：node-alpha 提供计算，node-beta 提供建图合同。")
    route: tuple[str, dict[str, Any]] | None = None
    if "unavailable" in question:
        route = "read_mission", {"mission_id": "mission-unavailable"}
    elif "第二个" in question:
        route = "read_node", {"node_id": "node-beta"}
    elif "任务" in question:
        route = "read_active_missions", {}
    elif "节点" in question:
        route = "read_cluster", {}
    elif "巡检" in question:
        route = "propose_action", {"action": "plan"}
    if route is not None:
        name, arguments = route
        return {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": f"owned-call-{number}",
                    "name": name,
                    "arguments": json.dumps(arguments),
                }
            ],
        }
    responses = {
        "你好": "你好！今天想聊些什么？",
        "谢谢": "不客气！",
        "好的": "好。",
        "我想聊聊怎么安排今天的工作": "可以，今天最想完成的事情是什么？我们可以一起排个顺序。",
        "你是谁？": "我是 RoboGuide，可以和你讨论事情，也能在需要时查询集群。",
    }
    return final_message(responses.get(question, "可以，接着说。"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep an authorized Provider request on the selected origin."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        """Refuse redirected requests rather than forwarding a credential elsewhere."""
        return None


def provider_handler(
    trace: Trace, remote: str | None, model: str, secret: str, seconds: float
) -> type[JsonHandler]:
    """Record native requests and either serve fixtures or forward actual model inference."""

    class Provider(JsonHandler):
        """Retain request/response bodies without persisting Authorization or credential values."""

        def do_POST(self) -> None:
            """Make exactly one Provider request for each actual CLI model request."""
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_HTTP:
                self.send_json(413, {"error": "request outside gate body limit"})
                return
            payload = json.loads(self.rfile.read(length))
            entry: dict[str, Any] = {"request": payload, "started_at": time.time()}
            with trace.lock:
                trace.provider.append(entry)
                number = len(trace.provider)
            try:
                if remote is None:
                    result = fixture_response(payload, number)
                else:
                    payload["model"] = model
                    headers = {"Content-Type": "application/json"}
                    if secret:
                        headers["Authorization"] = "Bearer " + secret
                    request = urllib.request.Request(
                        remote.rstrip("/") + "/responses",
                        data=json.dumps(payload, ensure_ascii=False).encode(),
                        headers=headers,
                        method="POST",
                    )
                    opener = urllib.request.build_opener(NoRedirect())
                    with opener.open(request, timeout=seconds) as response:
                        raw = response.read(MAX_HTTP + 1)
                        if len(raw) > MAX_HTTP:
                            raise ValueError("Provider response exceeded gate limit")
                        result = json.loads(raw)
                entry["response"] = result
                entry["elapsed_seconds"] = round(time.time() - entry["started_at"], 3)
                self.send_json(200, result)
            except Exception as error:
                entry["error_type"] = type(error).__name__
                entry["elapsed_seconds"] = round(time.time() - entry["started_at"], 3)
                with suppress(BrokenPipeError, ConnectionResetError):
                    self.send_json(502, {"error": "Provider inference unavailable in this gate"})

    return Provider


class ChatProcess:
    """Drive one real installed CLI process, recording complete PTY output and persisted turns."""

    def __init__(
        self, cli: Path, output: Path, profile: Path, session: str, resume: bool, seconds: float
    ) -> None:
        """Start an isolated shell from a directory without repository prompt/config assets."""
        self.output, self.session, self.seconds = output, session, seconds
        self.closed = False
        self.raw = bytearray()
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, 0x5414, struct.pack("HHHH", 32, 110, 0, 0))
        environment = dict(os.environ)
        environment.pop("OPENAI_API_KEY", None)
        environment.update(TERM="xterm-256color", PROMPT_TOOLKIT_NO_CPR="1")
        self.process = subprocess.Popen(
            [
                str(cli),
                "chat",
                "--profile",
                str(profile),
                "--resume" if resume else "--session",
                session,
            ],
            cwd=output,
            env=environment,
            stdin=slave,
            stdout=slave,
            stderr=slave,
        )
        os.close(slave)
        self.wait(lambda: "你 >" in self.text(), "initial input composer")

    def text(self) -> str:
        """Return readable terminal bytes while retaining the exact ANSI transcript separately."""
        return ANSI.sub("", self.raw.decode(errors="replace")).replace("\r", "")

    def pump(self) -> None:
        """Read available terminal output without a long blocking wait."""
        if select.select([self.master], [], [], 0.05)[0]:
            try:
                self.raw.extend(os.read(self.master, 65536))
            except OSError:
                pass

    def wait(self, predicate: Callable[[], bool], label: str) -> None:
        """Bound a software observation by one original deadline and persist evidence on failure."""
        deadline = time.monotonic() + self.seconds
        while time.monotonic() < deadline:
            self.pump()
            if predicate():
                return
            if self.process.poll() is not None:
                break
        self.persist()
        raise AssertionError(f"timed out waiting for {label}; evidence at {self.output}")

    def endings(self) -> int:
        """Count flushed Assistant response endings, excluding input and progress echoes."""
        return len(re.findall(r"\n──\n", self.text()))

    def send(self, text: str) -> None:
        """Enter a complete user message through the actual terminal input handler."""
        os.write(self.master, text.encode() + b"\r")

    def question(self, text: str) -> dict[str, Any]:
        """Wait for one completely displayed and persisted response to the actual user message."""
        before = self.endings()
        started = time.monotonic()
        self.send(text)
        self.wait(lambda: self.endings() > before, text)
        data = json.loads((self.output / "sessions" / f"{self.session}.json").read_text())
        return {
            "user": text,
            "assistant": data["messages"][-1]["text"],
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }

    def persist(self) -> None:
        """Keep both exact terminal bytes and a readable transcript for human review."""
        suffix = "resume" if (self.output / "terminal.ansi").exists() else "terminal"
        (self.output / f"{suffix}.ansi").write_bytes(self.raw)
        (self.output / f"{suffix}.txt").write_text(self.text(), encoding="utf-8")

    def close(self) -> None:
        """Request normal CLI shutdown and drain the original process before releasing its PTY."""
        if self.closed:
            return
        try:
            if self.process.poll() is None:
                self.send("/quit")
                self.process.wait(timeout=self.seconds)
            assert self.process.returncode == 0, self.process.returncode
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=10)
            self.persist()
            os.close(self.master)
            self.closed = True


def assert_natural(text: str) -> None:
    """Reject exposed protocol labels and canned operational menus in ordinary chat replies."""
    for marker in (
        "active_missions",
        "function_call",
        "查询预算",
        "Query JSON",
        "[E1]",
        "查询当前可见节点",
        "当前有哪些任务在运行",
    ):
        assert marker not in text, (marker, text)


def question_step(chat: ChatProcess, trace: Trace, text: str) -> dict[str, Any]:
    """Attach observed model and HTTP counts to this actual terminal turn, including greetings."""
    before = len(trace.provider), len(trace.reads), len(trace.writes)
    row = chat.question(text)
    row.update(
        provider_calls=len(trace.provider) - before[0],
        controller_reads=len(trace.reads) - before[1],
        controller_writes=len(trace.writes) - before[2],
    )
    return row


def package_provenance(cli: Path, wheel: Path | None) -> dict[str, Any]:
    """Inspect the installed package in its own interpreter and match optional exact wheel bytes."""
    interpreter = cli.resolve().parent / "python"
    if not interpreter.is_file():
        raise ValueError("--cli must be an installed entry point beside its Python interpreter")
    code = (
        "import hashlib,json,pathlib,roboguide_workbench;"
        "root=pathlib.Path(roboguide_workbench.__file__).parent;"
        "print(json.dumps({'location':str(root),'modules':"
        "{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.py')}}))"
    )
    inspected = subprocess.run(
        [str(interpreter), "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    result: dict[str, Any] = json.loads(inspected.stdout)
    if wheel is not None:
        with zipfile.ZipFile(wheel) as archive:
            expected = {
                Path(name).name: hashlib.sha256(archive.read(name)).hexdigest()
                for name in archive.namelist()
                if name.startswith("roboguide_workbench/") and name.endswith(".py")
            }
        assert "agent_loop.py" in expected, "wheel does not contain the new agent loop"
        assert result["modules"] == expected, "installed workbench differs from the supplied wheel"
        result["wheel"] = str(wheel.resolve())
        result["wheel_sha256"] = digest(wheel)
        result["matching_module_count"] = len(expected)
    return result


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    """Run conversational and fault scenarios against one unchanged installed CLI artifact."""
    output: Path = arguments.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    package = package_provenance(arguments.cli, arguments.wheel)
    trace = Trace()
    remote = arguments.provider_base_url if arguments.real_model else None
    secret = os.environ.get(arguments.api_key_env, "") if remote else ""
    if remote:
        parsed = urlsplit(remote)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Provider must be an explicit HTTP(S) base URL without credentials/query"
            )
        if (
            parsed.scheme == "http"
            and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            and not arguments.allow_insecure_model_http
        ):
            raise ValueError("remote HTTP Provider requires --allow-insecure-model-http")
        if not secret and not arguments.no_auth:
            raise ValueError("configured Provider credential environment variable is empty")
    transcript: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "passed": False,
        "mode": "real-model" if remote else "offline-fixture",
        "controller": "synthetic loopback HTTP facade; no field deployment",
        "transcript": transcript,
    }
    chat: ChatProcess | None = None
    try:
        with (
            serve(controller_handler(trace)) as controller,
            serve(
                provider_handler(trace, remote, arguments.model, secret, arguments.provider_seconds)
            ) as provider,
        ):
            model = output / "model.toml"
            model.write_text(
                f'[model]\nbase_url = "{provider}/v1"\nname = "{arguments.model}"\n'
                'requires_auth = false\nreasoning_effort = "low"\n'
                f"timeout_seconds = {arguments.provider_seconds}\n"
            )
            profile = output / "profile.toml"
            profile.write_text(
                f'[workbench]\ncontroller = "{controller}"\nmission_service = "{controller}"\n'
                'tenant = "fixture"\nconfig = "model.toml"\nsession_dir = "sessions"\n'
            )
            chat = ChatProcess(
                arguments.cli.resolve(),
                output,
                profile,
                "conversation",
                False,
                arguments.turn_seconds,
            )
            for text in ("你好", "谢谢", "好的", "我想聊聊怎么安排今天的工作"):
                before = len(trace.reads)
                row = question_step(chat, trace, text)
                assert_natural(row["assistant"])
                assert len(trace.reads) == before, row
                assert row["provider_calls"] >= 1, "ordinary chat bypassed the configured model"
                transcript.append(row)
            for text in ("你好，帮我查一下节点", "第二个节点呢？", "当前有没有任务在运行？"):
                before = len(trace.reads)
                row = question_step(chat, trace, text)
                assert len(trace.reads) == before + 1, row
                assert row["provider_calls"] >= 2, "native tool output did not return to the model"
                assert "active_missions" not in row["assistant"], row
                if "任务" in text:
                    assert "mission-running" in row["assistant"], row
                    assert "mission-history-001" not in row["assistant"], row
                transcript.append(row)
            row = question_step(chat, trace, "请查询 mission-unavailable 的当前状态")
            assert any("mission-unavailable" in entry["path"] for entry in trace.reads)
            assert any(
                word in row["assistant"]
                for word in ("未知", "无法", "未能", "失败", "不可用", "unknown", "Unknown")
            ), row
            transcript.append(row)
            row = question_step(chat, trace, "帮我制定一个巡检计划")
            assert trace.writes == [], trace.writes
            transcript.append(row)
            trace.entered.clear()
            trace.release.clear()
            trace.delay_next_inventory = True
            before = chat.endings()
            chat.send("/nodes")
            chat.wait(trace.entered.is_set, "slow Controller GET")
            chat.send("谢谢")
            active_chat = chat
            chat.wait(lambda: "已加入待处理队列" in active_chat.text(), "queued ordinary message")
            trace.release.set()
            chat.wait(
                lambda: active_chat.endings() >= before + 2, "original and queued response drain"
            )
            data = json.loads((output / "sessions/conversation.json").read_text())
            assert [row["text"] for row in data["messages"] if row["role"] == "user"][-2:] == [
                "/nodes",
                "谢谢",
            ]
            transcript.append(
                {
                    "user": "[during /nodes] 谢谢",
                    "assistant": data["messages"][-1]["text"],
                    "queued_once": True,
                }
            )
            transcript.append(question_step(chat, trace, "/feedback"))
            bundles = list((output / "sessions/feedback").glob("*.zip"))
            assert len(bundles) == 1
            with zipfile.ZipFile(bundles[0]) as archive:
                assert json.loads(archive.read("manifest.json"))["local_only"] is True
            chat.close()
            chat = ChatProcess(
                arguments.cli.resolve(),
                output,
                profile,
                "conversation",
                True,
                arguments.turn_seconds,
            )
            row = question_step(chat, trace, "谢谢")
            assert_natural(row["assistant"])
            assert row["provider_calls"] >= 1, row
            transcript.append({**row, "resumed": True})
            chat.close()
            chat = None
            assert trace.writes == [], trace.writes
            result.update(
                passed=True,
                feedback_bundle=str(bundles[0]),
                pty="actual installed CLI; 110 columns x 32 rows; automated input",
                controller_reads=len(trace.reads),
                controller_writes=trace.writes,
                provider_calls=len(trace.provider),
                ordinary_chat_controller_reads=0,
                resume=True,
                queue_exactly_once=True,
            )
    finally:
        trace.release.set()
        if chat is not None:
            chat.close()
        save(output / "controller-requests.json", trace.reads)
        save(output / "controller-writes.json", trace.writes)
        save(output / "provider-exchanges.json", trace.provider)
        result["cli"] = str(arguments.cli.resolve())
        result["cli_sha256"] = digest(arguments.cli.resolve())
        result["installed_package"] = package
        result["source_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2], text=True
        ).strip()
        if arguments.wheel:
            result["wheel"] = str(arguments.wheel.resolve())
            result["wheel_sha256"] = digest(arguments.wheel)
        save(output / "summary.json", result)
    return result


def main() -> None:
    """Require an explicit installed entry point and private fresh evidence output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--provider-base-url")
    parser.add_argument("--model", default="fixture-agent")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--no-auth", action="store_true")
    parser.add_argument("--allow-insecure-model-http", action="store_true")
    parser.add_argument("--provider-seconds", type=float, default=40)
    parser.add_argument("--turn-seconds", type=float, default=70)
    arguments = parser.parse_args()
    if arguments.real_model and not arguments.provider_base_url:
        parser.error("--real-model requires --provider-base-url")
    if not 0 < arguments.provider_seconds <= 60 or not 0 < arguments.turn_seconds <= 120:
        parser.error("timeouts exceed bounded software gate limits")
    print(json.dumps(run(arguments), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
