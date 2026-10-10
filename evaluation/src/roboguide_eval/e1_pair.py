"""Run one frozen native-EMOS/RoboGuide pair through separate owned processes.

This harness selects workloads and observes evidence. It never imports simulator
code, authors plans, retries consumed arms or decides official success. Protocol A
discloses the controlled local execution differences alongside pair validation.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

from roboguide_eval.accounting import AccountingProxyConfig, read_accounting_log
from roboguide_eval.b1_deployment import load_deployment
from roboguide_eval.b1_workload import load_b1_workload
from roboguide_eval.e1_attempt import terminate_owned_session
from roboguide_eval.e1_batch import _capture_log, file_digest, read_json, write_json
from roboguide_eval.e1_fairness import (
    ArmName,
    EvidenceStatus,
    ObservedField,
    PopulationManifest,
    PopulationRow,
    RequestedIntent,
    RunPairingEvidence,
    digest,
    load_population_manifest,
    validate_pair,
)
from roboguide_eval.models import JSONObject, JSONValue
from roboguide_eval.provider_capture import BodyCapture, CaptureProxyServer

SCHEMA = "roboguide.e1.pair-worker/v0.1"
_PORT_NAMES = ("proxy", "grpc", "controller", "artifact", "endpoint_a", "endpoint_b", "mission")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
_VENDOR_CODE = ("habitat-lab", "habitat-baselines", "habitat-mas")


def writable_asset_paths(config: JSONObject) -> tuple[str, ...]:
    """Validate deployment-declared vendor output paths without probing credentials."""
    raw = config.get("vendor_writable_assets", [])
    if (
        not isinstance(raw, list)
        or len(raw) > 16
        or any(
            not isinstance(value, str)
            or not value.startswith("data/")
            or value.endswith("/")
            or Path(value).is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or Path(value).suffix != ".json"
            for value in raw
        )
        or len(raw) != len(set(raw))
    ):
        raise ValueError("vendor writable assets must be distinct relative data JSON files")
    return tuple(cast(str, value) for value in raw)


def object_value(value: JSONValue) -> JSONObject:
    """Return an object or an explicit empty optional observation."""
    return value if isinstance(value, dict) else {}


def optional_document(path: Path) -> JSONObject:
    """Leave absent evidence unknown while rejecting malformed existing artifacts."""
    return read_json(path) if path.is_file() else {}


def required_text(document: JSONObject, name: str) -> str:
    """Require a nonblank public configuration string."""
    value = document.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid pair field: " + name)
    return value


@dataclass(frozen=True)
class PairSpec:
    """Freeze one pair's identities, source gates, ports and process budgets."""

    population: PopulationManifest
    row: PopulationRow
    ordinal: int
    config: JSONObject
    sources: Mapping[Path, str]
    ports: Mapping[str, int]

    @classmethod
    def load(cls, population_path: Path, config_path: Path, pair_id: str) -> PairSpec:
        """Check frozen configuration and workload agreement before any SUT process starts."""
        population = load_population_manifest(population_path)
        config = read_json(config_path)
        if (
            config.get("schema_version") != SCHEMA
            or config.get("population_digest") != population.digest
        ):
            raise ValueError("pair configuration/population identity mismatch")
        rows = [
            (i, row) for i, row in enumerate(population.selector.rows) if row.pair_id == pair_id
        ]
        if len(rows) != 1 or not _SAFE_ID.fullmatch(pair_id):
            raise ValueError("unknown or unsafe pair identity")
        ordinal, row = rows[0]
        if row.expected_episode_id is None or row.expected_scene_id is None or row.seed is None:
            raise ValueError("pair requires explicit episode, scene and seed")
        for name in (
            "code_root",
            "vendor_root",
            "habitat_python",
            "dataset_path",
            "input_directory",
        ):
            if not Path(required_text(config, name)).is_absolute():
                raise ValueError("pair paths must be absolute")
        for name in ("native_config", "b1_runner", "mission_config"):
            relative = Path(required_text(config, name))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe relative pair configuration path")
        if not re.fullmatch(r"[a-f0-9]{40}", required_text(config, "code_sha")):
            raise ValueError("pair code SHA must be complete")
        for name, limit in (
            ("arm_timeout_seconds", 86400),
            ("mi_observation_seconds", 86400),
            ("mission_observation_seconds", 86400),
            ("provider_timeout_seconds", 86400),
        ):
            value = config.get(name)
            if type(value) not in (int, float) or not 0 < cast(float, value) <= limit:
                raise ValueError("invalid pair budget: " + name)
        if type(config.get("gpu_device")) is not int or cast(int, config["gpu_device"]) < 0:
            raise ValueError("invalid GPU device")
        raw_ports = object_value(config.get("ports"))
        if set(raw_ports) != set(_PORT_NAMES) or any(
            type(p) is not int or not 1 <= p <= 65535 for p in raw_ports.values()
        ):
            raise ValueError("pair requires seven explicit ports")
        ports = {name: cast(int, value) for name, value in raw_ports.items()}
        if len(set(ports.values())) != len(ports):
            raise ValueError("pair ports overlap")
        sources: dict[Path, str] = {}
        for name, value in object_value(config.get("source_sha256")).items():
            if (
                not Path(name).is_absolute()
                or name.endswith(".env")
                or not isinstance(value, str)
                or not re.fullmatch(r"[a-f0-9]{64}", value)
            ):
                raise ValueError("invalid public pair source digest")
            sources[Path(name)] = value
        mandatory = [
            Path(required_text(config, "dataset_path")),
            Path(required_text(config, "habitat_python")),
        ]
        code = Path(required_text(config, "code_root"))
        if "b1_deployment" in config:
            relative = Path(required_text(config, "b1_deployment"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe relative B1 deployment path")
            selected = code / relative
            if not selected.resolve(strict=True).is_relative_to(code.resolve()):
                raise ValueError("B1 deployment resolves outside the frozen code view")
            mandatory.append(selected)
        mandatory += [
            code / "target/debug" / name for name in ("integration-server", "roboguide-node")
        ]
        mandatory += [
            code / required_text(config, name) for name in ("b1_runner", "mission_config")
        ]
        mandatory.append(
            Path(required_text(config, "vendor_root"))
            / "habitat-baselines/habitat_baselines/config"
            / required_text(config, "native_config")
        )
        for relative_asset in writable_asset_paths(config):
            asset = Path(required_text(config, "vendor_root")) / relative_asset
            if asset.exists():
                mandatory.append(asset)
        workload_path = Path(required_text(config, "input_directory")) / f"{pair_id}.json"
        if not all(path in sources for path in (*mandatory, workload_path)):
            raise ValueError("pair executable, configuration or workload source gate missing")
        runtime = object_value(config.get("runtime_modules"))
        if not runtime or any(
            not isinstance(path, str) or Path(path) not in sources for path in runtime.values()
        ):
            raise ValueError("pair requires frozen runtime module paths")
        workload = load_b1_workload(workload_path)
        if (
            workload.episode_id,
            workload.scene_id,
            workload.seed,
            "sha256:" + workload.dataset_sha256,
        ) != (
            row.expected_episode_id,
            row.expected_scene_id,
            row.seed,
            population.dataset.file_sha256,
        ) or workload.dataset_revision != population.dataset.revision:
            raise ValueError("B1 input differs from frozen population row")
        upstream = required_text(config, "provider_upstream")
        AccountingProxyConfig(upstream, Path("unused"))
        mission_config = tomllib.loads(
            (code / required_text(config, "mission_config")).read_text(encoding="utf-8")
        )
        llm = mission_config.get("mission", {}).get("llm", {})
        provider = mission_config.get("model_providers", {}).get(llm.get("model_provider"), {})
        if (
            llm.get("model") != population.model_configuration.model
            or llm.get("review_model") != population.model_configuration.model
            or provider.get("base_url", "").rstrip("/") != upstream.rstrip("/")
            or provider.get("wire_api") != "responses"
        ):
            raise ValueError("frozen MI model or Provider configuration differs from population")
        spec = cls(population, row, ordinal, config, sources, ports)
        spec.verify_sources()
        if "b1_deployment" in config:
            declaration = load_deployment(
                code, declaration_path=code / required_text(config, "b1_deployment")
            )
            if declaration.habitat_config != (
                "habitat-baselines/habitat_baselines/config/"
                + required_text(config, "native_config")
            ):
                raise ValueError("B1 deployment and native Habitat configuration differ")
            steps = population.benchmark_authority.parameters.get("max_episode_steps")
            if type(steps) is not int or steps != declaration.max_steps:
                raise ValueError("B1 deployment and frozen benchmark step budget differ")
        spec.source_identities()
        return spec

    def runtime_failures(self, manifest: JSONObject, vendor_view: Path) -> list[str]:
        """Check the actual interpreter/module origins against the exact frozen public files."""
        failures: list[str] = []
        executable = manifest.get("python_executable")
        if (
            not isinstance(executable, str)
            or Path(executable).resolve() != self.path("habitat_python").resolve()
        ):
            failures.append("runtime_interpreter_identity_unconfirmed")
        actual_modules = object_value(manifest.get("modules"))
        for name, path_value in object_value(self.config.get("runtime_modules")).items():
            expected = Path(cast(str, path_value))
            relative = (
                expected.relative_to(self.path("vendor_root"))
                if expected.is_relative_to(self.path("vendor_root"))
                else None
            )
            origin = (
                vendor_view / relative
                if relative is not None and relative.parts[0] in _VENDOR_CODE
                else expected
            )
            actual = object_value(actual_modules.get(name))
            path = actual.get("path")
            if (
                not isinstance(path, str)
                or Path(path).resolve() != origin.resolve()
                or (
                    relative is not None
                    and relative.parts[0] in _VENDOR_CODE
                    and not origin.resolve().is_relative_to(vendor_view.resolve())
                )
                or actual.get("sha256") != self.sources[expected]
                or not origin.is_file()
                or file_digest(origin) != self.sources[expected]
            ):
                failures.append("runtime_module_identity_unconfirmed:" + name)
        return failures

    def verify_vendor_view(self, directory: Path) -> None:
        """Fence each private code copy against frozen bytes before and after execution."""
        source = self.path("vendor_root")
        for path, expected in self.sources.items():
            if not path.is_relative_to(source):
                continue
            relative = path.relative_to(source)
            if relative.parts[0] not in _VENDOR_CODE:
                continue
            actual = directory / relative
            if (
                not actual.is_file()
                or not actual.resolve().is_relative_to(directory.resolve())
                or file_digest(actual) != expected
            ):
                raise ValueError("private vendor source differs from frozen pair source")

    def path(self, name: str) -> Path:
        """Resolve one already validated public absolute path."""
        return Path(required_text(self.config, name))

    def verify_sources(self) -> None:
        """Rehash frozen public sources before and after arms; credentials are excluded."""
        if any(file_digest(path) != expected for path, expected in self.sources.items()):
            raise ValueError("frozen pair source changed")

    def source_identities(self) -> JSONObject:
        """Derive identity digests from actual public files, never copy declared observations."""
        groups = object_value(self.config.get("identity_files"))
        expected_groups = {"task_spec", "habitat_config", "benchmark_authority", "stage2"}
        if set(groups) != expected_groups:
            raise ValueError("pair requires exact public identity file groups")
        hashes: dict[str, JSONObject] = {}
        for name, raw in groups.items():
            files = object_value(raw)
            if not files:
                raise ValueError("empty identity source group")
            actual: JSONObject = {}
            for label, path_value in files.items():
                if not isinstance(path_value, str) or Path(path_value) not in self.sources:
                    raise ValueError("identity file must have a frozen source gate")
                actual[label] = "sha256:" + file_digest(Path(path_value))
            hashes[name] = actual
        task = self.population.task
        authority = self.population.benchmark_authority
        stage2 = self.population.stage2_identity
        if (
            digest(hashes["task_spec"]) != task.task_spec_digest
            or digest(hashes["habitat_config"]) != task.habitat_config_digest
            or digest(hashes["benchmark_authority"]) != authority.implementation_digest
            or hashes["stage2"] != dict(stage2.file_digests)
        ):
            raise ValueError("actual source identity differs from population declaration")
        return {
            "task_spec_identity": {
                "benchmark": task.benchmark,
                "task": task.task,
                "task_spec_digest": digest(hashes["task_spec"]),
            },
            "habitat_config_identity": {"habitat_config_digest": digest(hashes["habitat_config"])},
            "benchmark_authority_identity": {
                "measure": authority.measure,
                "implementation_digest": digest(hashes["benchmark_authority"]),
                "parameters": dict(authority.parameters),
            },
            "stage2_identity": {
                "checkout_commit": stage2.checkout_commit,
                "file_digests": hashes["stage2"],
            },
            "simulator_identity": self.population.simulator_identity.to_json(),
        }

    def order(self) -> tuple[ArmName, ArmName]:
        """Alternate the first arm by frozen population ordinal without outcome adaptation."""
        return ("emos", "roboguide") if self.ordinal % 2 == 0 else ("roboguide", "emos")


def ports_available(ports: Mapping[str, int]) -> None:
    """Check all loopback listeners without stopping or replacing another process."""
    sockets: list[socket.socket] = []
    try:
        for port in ports.values():
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sockets.append(listener)
            listener.bind(("127.0.0.1", port))
    finally:
        for listener in sockets:
            listener.close()


def wait_ports_closed(ports: Mapping[str, int], budget: float = 30) -> bool:
    """Wait for owned listener cleanup without killing or adopting another process."""
    deadline = time.monotonic() + budget
    while True:
        try:
            ports_available(ports)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)


def private_vendor_view(
    source: Path, directory: Path, writable_assets: tuple[str, ...] = ()
) -> None:
    """Copy unchanged code and isolate declared vendor writes from shared dataset assets.

    Only the ancestors of declared relative data files become private directories.
    Existing write targets are byte-copied; other assets remain shared references.
    This does not modify the original configuration or initialization RNG.
    """
    targets = tuple(Path(value) for value in writable_assets)
    if len(targets) > 16 or any(
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) < 2
        or path.parts[0] != "data"
        or path.suffix != ".json"
        for path in targets
    ):
        raise ValueError("vendor writable assets must be bounded relative data JSON files")
    for path in targets:
        resolved = (source / path).resolve()
        if not resolved.is_relative_to((source / "data").resolve()):
            raise ValueError("vendor writable asset escapes the shared data root")
    directory.mkdir()
    for name in ("data", *_VENDOR_CODE):
        if not (source / name).is_dir():
            raise ValueError("required vendor directory unavailable")
        if name == "data":
            if targets:
                _private_asset_branch(source, directory, Path("data"), targets)
            else:
                (directory / name).symlink_to(source / name, target_is_directory=True)
        else:
            shutil.copytree(
                source / name,
                directory / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )


def _private_asset_branch(
    source: Path, directory: Path, branch: Path, targets: tuple[Path, ...]
) -> None:
    """Materialize only write-path ancestors and preserve original target bytes privately."""
    destination = directory / branch
    destination.mkdir()
    original = source / branch
    names = {path.name for path in original.iterdir()} if original.is_dir() else set()
    names.update(path.parts[len(branch.parts)] for path in targets if branch in path.parents)
    for name in sorted(names):
        child = branch / name
        if child in targets:
            if (source / child).exists():
                if not (source / child).is_file():
                    raise ValueError("vendor writable asset is not a file")
                shutil.copyfile(source / child, directory / child)
        elif any(child in path.parents for path in targets):
            _private_asset_branch(source, directory, child, targets)
        else:
            (directory / child).symlink_to(
                (source / child).resolve(), target_is_directory=(source / child).is_dir()
            )


def arm_environment(spec: PairSpec, directory: Path, proxy_port: int) -> dict[str, str]:
    """Remove inherited experiment switches; credentials remain environment-only."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(
            ("ROBOGUIDE_B1_", "ROBOGUIDE_HABITAT_", "ROBOGUIDE_MISSION_", "EMOS_LLM_", "OPENAI_")
        )
    }
    credential = os.environ.get("OPENAI_API_KEY")
    if not credential:
        raise ValueError("authorized OPENAI_API_KEY environment is required")
    env.pop("VIRTUAL_ENV", None)
    env.pop("VIRTUAL_ENV_PROMPT", None)
    env["PATH"] = os.pathsep.join(
        part for part in env.get("PATH", "").split(os.pathsep) if ".venv" not in part
    )
    code, vendor = spec.path("code_root"), directory / "vendor-cwd"
    env.update(
        OPENAI_API_KEY=credential,
        OPENAI_BASE_URL=f"http://127.0.0.1:{proxy_port}/v1",
        EMOS_LLM_MODEL=spec.population.model_configuration.model,
        CUDA_VISIBLE_DEVICES=str(spec.config["gpu_device"]),
        ROBOGUIDE_HABITAT_CUDA_DEVICE=str(spec.config["gpu_device"]),
        ROBOGUIDE_EMOS_ROOT=str(vendor),
        ROBOGUIDE_MISSION_CONFIG=str(directory / "mission-config.toml"),
        ROBOGUIDE_B1_PHYSICAL_DIAGNOSTICS="1",
        ROBOGUIDE_HABITAT_CAPTURE_VIDEO="1",
        ROBOGUIDE_B1_MI_OBSERVATION_BUDGET_SECONDS=str(spec.config["mi_observation_seconds"]),
        ROBOGUIDE_B1_MISSION_OBSERVATION_BUDGET_SECONDS=str(
            spec.config["mission_observation_seconds"]
        ),
        PYTHONPATH=os.pathsep.join(
            str(path)
            for path in (
                code / "integrations/habitat-local-eaios",
                vendor / "habitat-lab",
                vendor / "habitat-baselines",
                vendor / "habitat-mas",
            )
        ),
        MPLCONFIGDIR=str(directory / "mpl"),
        HABITAT_SIM_LOG="quiet",
        MAGNUM_LOG="quiet",
    )
    if "b1_deployment" in spec.config:
        env["ROBOGUIDE_B1_DEPLOYMENT"] = str(code / required_text(spec.config, "b1_deployment"))
    for name, field in (
        ("CONTROLLER_GRPC", "grpc"),
        ("CONTROLLER", "controller"),
        ("ARTIFACT", "artifact"),
        ("HABITAT", "endpoint_a"),
        ("HABITAT_PORT_B", "endpoint_b"),
        ("MISSION", "mission"),
    ):
        env["ROBOGUIDE_B1_" + (name if name.endswith("PORT_B") else name + "_PORT")] = str(
            spec.ports[field]
        )
    return env


def arm_command(spec: PairSpec, arm: ArmName, directory: Path) -> tuple[str, ...]:
    """Build the original native evaluator or one production B1 Runner invocation."""
    if arm == "roboguide":
        return (
            "bash",
            str(spec.path("code_root") / required_text(spec.config, "b1_runner")),
            str(directory / "run"),
            str(spec.path("input_directory") / f"{spec.row.pair_id}.json"),
        )
    return (
        str(spec.path("habitat_python")),
        "-u",
        "-m",
        "habitat_local_eaios.native_workload",
        "--evidence-dir",
        str(directory / "native-evidence"),
        "--run-id",
        spec.row.pair_id + "-emos",
        "--episode-id",
        cast(str, spec.row.expected_episode_id),
        "--scene-id",
        cast(str, spec.row.expected_scene_id),
        "--dataset-path",
        str(spec.path("dataset_path")),
        "--dataset-sha256",
        spec.population.dataset.file_sha256.removeprefix("sha256:"),
        "--",
        "--config-name",
        required_text(spec.config, "native_config"),
        "habitat_baselines.evaluate=True",
        "habitat_baselines.num_environments=1",
        "habitat_baselines.test_episode_count=1",
        "habitat_baselines.eval.evals_per_ep=1",
        "habitat_baselines.torch_gpu_id=0",
        "habitat.simulator.habitat_sim_v0.gpu_device_id=0",
        f"habitat.seed={spec.row.seed}",
        "habitat.environment.iterator_options.shuffle=False",
        "habitat.environment.iterator_options.num_episode_sample=-1",
        f"habitat.dataset.data_path={spec.path('dataset_path')}",
        f"habitat_baselines.video_dir={directory / 'video'}",
        f"habitat_baselines.tensorboard_dir={directory / 'tensorboard'}",
        f"habitat_baselines.log_file={directory / 'native.log'}",
    )


class ResourceSampler:
    """Observe owned process GPU memory and wall time without scheduling authority."""

    def __init__(self, gpu: int) -> None:
        """Retain a device and a stoppable, bounded periodic observation."""
        self.gpu = gpu
        self.stop = threading.Event()
        self.peak = 0.0
        self.samples = 0
        self.unavailable = 0

    def observe(self, pid: int) -> None:
        """Sum graphics and compute memory in this exact session, rejecting unknown reads."""
        while not self.stop.is_set():
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        f"--id={self.gpu}",
                        "--query",
                        "--xml-format",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if len(result.stdout) > 2 * 1024 * 1024:
                    raise ValueError("GPU response exceeds observation budget")
                gpus = ElementTree.fromstring(result.stdout).findall("gpu")
                if len(gpus) != 1 or gpus[0].find("processes") is None:
                    raise ValueError("selected GPU process observation unavailable")
                records = gpus[0].findall("processes/process_info")
                if len(records) > 4096:
                    raise ValueError("GPU process observation budget exhausted")
                memory_by_pid: dict[int, float] = {}
                for record in records:
                    gpu_pid = int(record.findtext("pid", ""))
                    try:
                        if os.getsid(gpu_pid) != pid:
                            continue
                    except ProcessLookupError:
                        continue
                    memory = record.findtext("used_memory", "").split()
                    if len(memory) != 2 or memory[1] != "MiB":
                        raise ValueError("owned GPU process memory unavailable")
                    value = float(memory[0])
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("invalid owned GPU memory observation")
                    if gpu_pid in memory_by_pid and memory_by_pid[gpu_pid] != value:
                        raise ValueError("conflicting owned GPU process observations")
                    memory_by_pid[gpu_pid] = value
                total = sum(memory_by_pid.values())
                self.peak = max(self.peak, total)
                self.samples += 1
            except (OSError, ValueError, ElementTree.ParseError, subprocess.SubprocessError):
                self.unavailable += 1
            self.stop.wait(5)


def run_arm(spec: PairSpec, arm: ArmName, directory: Path) -> JSONObject:
    """Consume one isolated arm, drain observation and preserve original failure evidence."""
    spec.verify_sources()
    directory.mkdir()
    private_vendor_view(
        spec.path("vendor_root"), directory / "vendor-cwd", writable_asset_paths(spec.config)
    )
    spec.verify_vendor_view(directory / "vendor-cwd")
    credential = os.environ.get("OPENAI_API_KEY", "")
    capture = BodyCapture(directory / "provider-bodies", (credential,))
    proxy = CaptureProxyServer(
        ("127.0.0.1", spec.ports["proxy"]),
        AccountingProxyConfig(
            required_text(spec.config, "provider_upstream"),
            directory / "provider-accounting.jsonl",
            float(cast(float, spec.config["provider_timeout_seconds"])),
            spec.row.pair_id + "-" + arm,
        ),
        capture,
    )
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    source_config = spec.path("code_root") / required_text(spec.config, "mission_config")
    text = source_config.read_text(encoding="utf-8")
    upstream = required_text(spec.config, "provider_upstream").rstrip("/")
    replacement = text.replace(
        f'base_url = "{upstream}"', f'base_url = "http://127.0.0.1:{proxy.server_port}"'
    )
    process: subprocess.Popen[bytes] | None = None
    threads: list[threading.Thread] = []
    statuses: list[JSONObject] = []
    sampler = ResourceSampler(cast(int, spec.config["gpu_device"]))
    sample_thread: threading.Thread | None = None
    result: JSONObject = {"arm": arm, "directory": str(directory), "harness_error": None}
    started = time.monotonic()
    try:
        if text == replacement:
            raise ValueError("MI upstream configuration differs from frozen provider")
        (directory / "mission-config.toml").write_text(replacement, encoding="utf-8")
        env = arm_environment(spec, directory, proxy.server_port)
        argv = arm_command(spec, arm, directory)
        write_json(
            directory / "launch.json",
            {
                "argv": list(argv),
                "cwd": str(directory / "vendor-cwd"),
                "code_sha": spec.config["code_sha"],
                "requested_model": spec.population.model_configuration.model,
                "provider_upstream": upstream,
                "gpu_device": spec.config["gpu_device"],
                "credential_archived": False,
                "arm_timeout_seconds": spec.config["arm_timeout_seconds"],
                "population_digest": spec.population.digest,
            },
        )
        process = subprocess.Popen(
            argv,
            cwd=directory / "vendor-cwd",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        for stream, name in ((process.stdout, "stdout.log"), (process.stderr, "stderr.log")):
            status: JSONObject = {"closed": False}
            thread = threading.Thread(
                target=_capture_log,
                args=(stream, directory / name, (credential.encode(),), status),
                daemon=True,
            )
            thread.start()
            threads.append(thread)
            statuses.append(status)
        sample_thread = threading.Thread(target=sampler.observe, args=(process.pid,), daemon=True)
        sample_thread.start()
        try:
            process.wait(timeout=float(cast(float, spec.config["arm_timeout_seconds"])))
        except subprocess.TimeoutExpired:
            result["harness_error"] = "arm_wall_timeout"
            terminate_owned_session(process, grace_seconds=150)
        result["exit_code"] = process.returncode
    except (Exception, KeyboardInterrupt) as error:
        result["harness_error"] = type(error).__name__
        if isinstance(error, (KeyboardInterrupt, InterruptedError)):
            result["interrupted"] = True
    finally:
        if process is not None:
            terminate_owned_session(process, grace_seconds=150)
        sampler.stop.set()
        if sample_thread is not None:
            sample_thread.join(timeout=6)
        for thread in threads:
            thread.join(timeout=15)
        proxy.shutdown()
        drained = proxy.wait_for_idle(60)
        proxy.server_close()
        proxy_thread.join(timeout=5)
        if not wait_ports_closed(spec.ports):
            result["harness_error"] = "owned_service_ports_unclosed"
        closure: JSONObject = {
            "drained": drained,
            "accounting": cast(JSONObject, proxy.recording_status()),
            "body_capture": capture.status(close=True),
        }
        write_json(directory / "provider-closure.json", closure)
        result.update(
            elapsed_seconds=round(time.monotonic() - started, 3),
            resource_usage={
                "peak_gpu_mib": sampler.peak,
                "samples": sampler.samples,
                "unavailable_samples": sampler.unavailable,
            },
            log_capture=cast(list[JSONValue], statuses),
            provider_closure=closure,
        )
        write_json(directory / "arm-process-result.json", result)
    spec.verify_sources()
    spec.verify_vendor_view(directory / "vendor-cwd")
    return result


def _observed(value: JSONValue, source: str) -> ObservedField:
    """Keep unavailable facts explicit rather than substituting requested configuration."""
    return ObservedField(
        value, source, EvidenceStatus.AVAILABLE if value is not None else EvidenceStatus.UNAVAILABLE
    )


def collect_arm(
    spec: PairSpec, arm: ArmName, directory: Path, process: JSONObject
) -> tuple[JSONObject, RunPairingEvidence]:
    """Project immutable original evidence, retaining separate SUT, benchmark and archive facts."""
    verdict: JSONObject = {}
    steps_basis: str = "terminal_execution_evidence"
    if arm == "roboguide":
        run = directory / "run"
        initial = optional_document(run / "evidence/diagnostics-initial.json")
        world = optional_document(run / "evidence/shared-world-summary.json")
        start = optional_document(run / "evidence/relocation-episode-start.json")
        identity = object_value(start.get("identity"))
        verdict = optional_document(run / "b1-verdict.json")
        record = optional_document(run / "b1-request-record.json")
        mission = optional_document(run / "mission.json")
        official = world.get("official_pddl_success")
        steps = object_value(world.get("identity")).get("simulator_steps")
        status = mission.get("status", record.get("lifecycle"))
    else:
        roots = list((directory / "native-evidence").glob("worker-*"))
        run = roots[0] if len(roots) == 1 else directory / "unavailable-native-worker"
        initial = optional_document(run / "diagnostics-initial.json")
        identity = optional_document(run / "workload-selection.json")
        world = optional_document(run / "native-outcome.json")
        official, steps = world.get("official_pddl_success"), world.get("simulator_steps")
        if not world:
            progress = optional_document(run / "native-progress.json")
            if (
                progress.get("schema_version") == "roboguide.native-execution-progress/v0.1"
                and progress.get("basis") == "last_durable_successful_step_lower_bound"
                and progress.get("episode_id") == spec.row.expected_episode_id
                and progress.get("scene_id") == spec.row.expected_scene_id
                and type(progress.get("simulator_steps")) is int
                and cast(int, progress["simulator_steps"]) >= 0
            ):
                steps = progress["simulator_steps"]
                steps_basis = "last_durable_successful_step_lower_bound"
            else:
                steps_basis = "unavailable"
        status = (
            "Failed"
            if process.get("exit_code") not in (None, 0)
            else "Completed"
            if world.get("episode_terminal") is True
            else "Unavailable"
        )
    calls = (
        cast(list[JSONObject], read_accounting_log(directory / "provider-accounting.jsonl"))
        if (directory / "provider-accounting.jsonl").exists()
        else []
    )
    successful = [
        call for call in calls if call.get("status") == 200 and call.get("method") == "POST"
    ]
    model_verified = bool(successful) and all(
        call.get("requested_model")
        == call.get("response_model")
        == spec.population.model_configuration.model
        for call in successful
    )
    fatal: list[str] = []
    if process.get("harness_error") == "owned_service_ports_unclosed":
        fatal.append("owned_service_cleanup_incomplete")
    closure = object_value(process.get("provider_closure"))
    if closure.get("drained") is not True or any(
        object_value(closure.get(key)).get("complete") is not True
        for key in ("accounting", "body_capture")
    ):
        fatal.append("provider_archive_incomplete")
    if any(
        object_value(row).get("closed") is not True
        for row in cast(list[JSONValue], process.get("log_capture", []))
    ):
        fatal.append("process_log_archive_incomplete")
    for call in calls:
        if call.get("method") != "POST":
            continue
        if call.get("requested_model") != spec.population.model_configuration.model or (
            call.get("status") == 200
            and call.get("response_model") != spec.population.model_configuration.model
        ):
            fatal.append("provider_model_identity_unconfirmed")
        if call.get("status") in (400, 401, 403, 404):
            fatal.append("provider_configuration_failure")
    if (
        arm == "roboguide"
        and verdict
        and object_value(verdict.get("admission")).get("provenance_valid") is not True
    ):
        fatal.append("b1_provenance_invalid")
    if arm == "roboguide" and not verdict:
        fatal.append("b1_verdict_missing")
    if arm == "emos" and not initial and process.get("exit_code") != 0:
        fatal.append("native_startup_or_workload_selection_unavailable")
    runtime_path = (
        run / "evidence/runtime-source-manifest.json"
        if arm == "roboguide"
        else directory / "native-evidence/runtime-source-manifest.json"
    )
    runtime = optional_document(runtime_path)
    if successful or (type(steps) is int and steps > 0):
        fatal.extend(spec.runtime_failures(runtime, directory / "vendor-cwd"))
    if type(steps) is int and steps > 0 and not initial:
        fatal.append("physical_initial_archive_missing")
    if initial and (
        initial.get("episode_id"),
        initial.get("scene_id"),
        initial.get("habitat_seed_config"),
    ) != (spec.row.expected_episode_id, spec.row.expected_scene_id, spec.row.seed):
        fatal.append("observed_reset_identity_mismatch")
    actual_dataset = identity.get("dataset_sha256")
    if (
        actual_dataset is not None
        and actual_dataset != spec.population.dataset.file_sha256.removeprefix("sha256:")
    ):
        fatal.append("observed_dataset_mismatch")
    official_value = official if type(official) is bool else None
    incidents = [call for call in calls if call.get("status") != 200]
    infra = (
        process.get("harness_error") is not None
        or bool(fatal)
        or object_value(verdict.get("admission")).get("failure_owner") == "EXTERNAL_INFRA"
        or (bool(incidents) and official_value is None)
    )
    result: JSONObject = {
        "arm": arm,
        "system_outcome": status,
        "official_pddl_success": official_value,
        "official_outcome": "unavailable"
        if official_value is None
        else "true"
        if official_value
        else "false",
        "simulator_steps": steps,
        "simulator_steps_basis": steps_basis,
        "physical_episode_executed": steps > 0 if type(steps) is int else None,
        "admission": verdict.get("admission"),
        "semantic_goal_diagnostic": object_value(verdict.get("context")).get(
            "semantic_goal_diagnostic"
        ),
        "provider_calls": len(calls),
        "provider_incidents": cast(list[JSONValue], incidents),
        "model_identity_verified": model_verified,
        "fatal_reasons": cast(list[JSONValue], sorted(set(fatal))),
        "infrastructure_failure": infra,
        "process": process,
        "initial_snapshot": initial,
        "evidence_directory": str(run),
    }
    observed: dict[str, ObservedField] = {
        "dataset_identity": _observed(
            "sha256:" + actual_dataset if isinstance(actual_dataset, str) else None, str(run)
        ),
        "episode_identity": _observed(
            initial.get("episode_id"), str(run / "diagnostics-initial.json")
        ),
        "scene_identity": _observed(initial.get("scene_id"), str(run / "diagnostics-initial.json")),
        "population_manifest_identity": _observed(
            spec.population.digest, "validated frozen manifest"
        ),
        "model_configuration_identity": _observed(
            spec.population.model_configuration.to_json() if model_verified else None,
            str(directory / "provider-accounting.jsonl"),
        ),
    }
    # These identities come from rehashed original public files, not from outcome or model prose.
    for dimension, value in spec.source_identities().items():
        observed[dimension] = _observed(
            value,
            "frozen public source digests reverified before/after arm",
        )
    loaded = (
        object_value(
            optional_document(directory / "run/evidence/stage2-agent-identity.json").get(
                "robot_types"
            )
        )
        if arm == "roboguide"
        else object_value(identity.get("loaded_robot_types"))
    )
    embodiment: JSONObject | None = None
    if loaded:
        embodiment = {
            "agents": [
                {"index": agent.index, "handle": loaded.get(f"agent_{agent.index}")}
                for agent in spec.population.embodiment_profile.agents
            ]
        }
        if embodiment != spec.population.embodiment_profile.to_json():
            fatal.append("loaded_embodiment_mismatch")
            result["fatal_reasons"] = cast(list[JSONValue], sorted(set(fatal)))
            result["infrastructure_failure"] = True
    observed["embodiment_profile"] = _observed(
        embodiment, "loaded articulated robot class observation"
    )
    additional = {
        "local_execution_profile": _observed(
            optional_document(directory / "run/evidence/local-how-profile.json")
            if arm == "roboguide"
            else {"profile": "original-native-EMOS"},
            str(run),
        ),
        "actual_reset": _observed(initial or None, str(run / "diagnostics-initial.json")),
    }
    evidence = RunPairingEvidence.create(
        arm=arm,
        run_id=spec.row.pair_id + "-" + arm,
        pair_id=spec.row.pair_id,
        population_manifest_digest=spec.population.digest,
        requested=RequestedIntent(seed=spec.row.seed),
        observed=observed,
        additional_observations=additional,
        system_outcome=_observed(
            {"status": status, "official_pddl_success": official_value}, str(run)
        ),
    )
    write_json(directory / "run-pairing-evidence.json", evidence.to_json())
    write_json(directory / "arm-summary.json", result)
    return result, evidence


def compare_reset(left: JSONObject, right: JSONObject) -> JSONObject:
    """Compare actual positions, rotations and goal geometry; equal seeds alone prove nothing."""
    fields = (
        "episode_id",
        "scene_id",
        "habitat_seed_config",
        "agents",
        "goal_entity_positions",
        "goal_conjuncts",
    )

    def vector(value: JSONValue) -> bool:
        """Accept only recorded finite physical position vectors."""
        return (
            isinstance(value, list)
            and len(value) == 3
            and all(
                type(item) in (int, float) and math.isfinite(cast(float, item)) for item in value
            )
        )

    def project(snapshot: JSONObject) -> JSONObject:
        """Compare actual poses while leaving optional semantic-region gaps diagnostic-only."""
        value = dict(snapshot)
        agents = object_value(snapshot.get("agents"))
        poses: JSONObject = {}
        for name, raw in agents.items():
            agent = object_value(raw)
            rotation = agent.get("rotation")
            angle = object_value(rotation).get("value") if isinstance(rotation, dict) else rotation
            if (
                not vector(agent.get("position"))
                or type(angle) not in (float, int)
                or not math.isfinite(cast(float, angle))
            ):
                value.pop("agents", None)
                break
            poses[name] = {"position": agent["position"], "rotation": rotation}
        else:
            if poses:
                value["agents"] = poses
            else:
                value.pop("agents", None)
        positions = object_value(value.get("goal_entity_positions"))
        if not positions or not all(vector(position) for position in positions.values()):
            value.pop("goal_entity_positions", None)
        goals = value.get("goal_conjuncts")
        if (
            not isinstance(goals, list)
            or not goals
            or not all(isinstance(goal, str) and goal != "unavailable" for goal in goals)
        ):
            value.pop("goal_conjuncts", None)
        return value

    left_value, right_value = project(left), project(right)
    unavailable = [key for key in fields if key not in left_value or key not in right_value]
    differences = [
        key for key in fields if key not in unavailable and left_value[key] != right_value[key]
    ]
    return {
        "status": "unavailable" if unavailable else "mismatch" if differences else "matched",
        "unavailable_fields": cast(list[JSONValue], unavailable),
        "different_fields": cast(list[JSONValue], differences),
        "basis": "actual first returned reset snapshots, never inferred from seed",
    }


def run_pair(population_path: Path, config_path: Path, pair_id: str, output: Path) -> JSONObject:
    """Claim an unused pair directory, consume arms once and preserve non-comparable failures."""
    spec = PairSpec.load(population_path, config_path, pair_id)
    output.mkdir(parents=True, exist_ok=False)
    result: JSONObject = {
        "job_id": pair_id,
        "infrastructure_failure": False,
        "fatal_failure": False,
        "arms": [],
    }
    previous = signal.getsignal(signal.SIGTERM)

    def interrupted(signum: int, frame: object) -> None:
        """Stop the current owned arm without claiming success or starting another arm."""
        raise InterruptedError("pair interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    arms: dict[ArmName, JSONObject] = {}
    evidence: dict[ArmName, RunPairingEvidence] = {}
    try:
        write_json(
            output / "pair-manifest-input.json",
            {
                "population_digest": spec.population.digest,
                "worker_config_sha256": file_digest(config_path),
                "arm_order": list(spec.order()),
                "pair_id": pair_id,
                "code_sha": spec.config["code_sha"],
                "outcome_retries": 0,
                "protocol": spec.population.protocol,
            },
        )
        for arm in spec.order():
            ports_available(spec.ports)
            process = run_arm(spec, arm, output / arm)
            arms[arm], evidence[arm] = collect_arm(spec, arm, output / arm, process)
            result["arms"] = cast(list[JSONValue], list(arms.values()))
            result["infrastructure_failure"] = any(
                row.get("infrastructure_failure") is True for row in arms.values()
            )
            result["fatal_failure"] = any(bool(row.get("fatal_reasons")) for row in arms.values())
            write_json(output / "pair-progress.json", result)
            if result["fatal_failure"] or process.get("interrupted") is True:
                break
        if len(evidence) == 2:
            pair = validate_pair(spec.population, evidence["emos"], evidence["roboguide"])
            write_json(output / "pair-manifest.json", pair.to_json())
            reset = compare_reset(
                object_value(arms["emos"].get("initial_snapshot")),
                object_value(arms["roboguide"].get("initial_snapshot")),
            )
            result.update(pair_comparability=pair.comparability.value, reset_comparison=reset)
            result["comparison_eligible"] = (
                not result["fatal_failure"]
                and pair.comparability.value == "pair_comparable"
                and reset["status"] == "matched"
            )
            if reset["status"] == "mismatch":
                result.update(
                    fatal_failure=True,
                    infrastructure_failure=True,
                    reason="actual_reset_state_mismatch",
                )
        peaks = [
            object_value(object_value(row.get("process")).get("resource_usage"))
            for row in arms.values()
        ]
        result["resource_usage"] = {
            "both_arms_measured": len(peaks) == 2
            and all(
                cast(int, row.get("samples", 0)) > 0 and cast(float, row.get("peak_gpu_mib", 0)) > 0
                for row in peaks
            ),
            "peak_job_gpu_mib": max(
                (cast(float, row.get("peak_gpu_mib", 0)) for row in peaks), default=0
            ),
        }
    except (Exception, KeyboardInterrupt) as error:
        result.update(
            infrastructure_failure=True, fatal_failure=True, error_type=type(error).__name__
        )
    finally:
        signal.signal(signal.SIGTERM, previous)
        write_json(output / "pair-outcome.json", result)
    return result
