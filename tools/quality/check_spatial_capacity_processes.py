"""Exercise actual Spatial Unix worker capacity without geometry jobs or Nodes."""

from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import subprocess
from pathlib import Path
from typing import Any

from check_listener_capacity_processes import local_capacity, unix
from check_replica_content_processes import digest, save
from check_state_verifier_processes import ROOT, wait_for


def run(output: Path, python: Path, configuration: Path, sources: Path) -> dict[str, Any]:
    """Start and reap the real SDK service on explicit original input/profile paths."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = [
        python,
        configuration,
        Path(__file__),
        Path(__file__).with_name("check_listener_capacity_processes.py"),
        *sorted((ROOT / "integrations/spatial-provider/roboguide_spatial_provider").glob("*.py")),
    ]
    frozen = {str(path): digest(path) for path in files}
    save(output / "source-identity.json", frozen)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "integrations/spatial-provider")
    root = output / "backend"
    arguments = [
        str(python),
        "-m",
        "roboguide_spatial_provider.http_service",
        "--root",
        str(root),
        "--source-root",
        str(sources),
        "--output-root",
        str(output / "outputs"),
        "--config",
        str(configuration),
        "--connection-limit",
        "2",
        "--connection-timeout-seconds",
        "2",
    ]
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "geometry_jobs": 0,
        "arguments": arguments,
    }
    with (output / "spatial.log").open("wb") as log:
        process = subprocess.Popen(arguments, env=environment, cwd=ROOT, stdout=log, stderr=log)
    result["actual_pid"] = process.pid
    try:
        socket = root / "local.sock"
        wait_for(
            lambda: unix(socket, "/v1/transport").get("limit") == 2,
            "actual configured Spatial SDK service",
            60,
        )
        assert stat.S_IMODE(socket.stat().st_mode) == 0o600
        result["actual_capacity"] = local_capacity(socket, lambda: unix(socket, "/v1/transport"))
        result["actual_instance"] = json.loads((root / "instance.json").read_bytes())
        assert not list((root / "records").iterdir())
        assert {str(path): digest(path) for path in files} == frozen
        result["passed"] = True
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                result["forced_cleanup"] = True
        result["actual_wait"] = process.returncode
        result["passed"] = result["passed"] and process.returncode in {0, -signal.SIGINT}
        save(output / "result.json", result)
    return result


def main() -> None:
    """Require an explicit actual SDK interpreter and retained deployment input profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "python", "configuration", "sources"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if not run(
        args.output.resolve(),
        args.python.absolute(),
        args.configuration.resolve(strict=True),
        args.sources.resolve(strict=True),
    )["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
