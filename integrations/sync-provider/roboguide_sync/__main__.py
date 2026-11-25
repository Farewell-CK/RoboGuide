"""CLI entrypoint for `python -m roboguide_sync`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import threading
from pathlib import Path

from .adapters import HttpChassisAdapter, RobonixLite3Adapter, RobotMotionAdapter, SimAdapter
from .clock_sync import ClockProbe, FixedClockProbe, SystemClockProbe
from .http_server import SyncService
from .journal import SyncJournal
from .transport import UdpHub


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="roboguide_sync",
        description="EAIOS-neutral multi-robot synchronized motion & choreography provider",
    )
    p.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=18210, help="HTTP bind port (0 for ephemeral)")
    p.add_argument("--udp-bind", default="0.0.0.0", help="UDP peer bind host (default: 0.0.0.0)")
    p.add_argument(
        "--udp-port", type=int, default=18201, help="UDP peer bind port (0 for ephemeral)"
    )
    p.add_argument(
        "--advertise-ip",
        default="127.0.0.1",
        help="Peer IP/hostname advertised via GET /v1/sync/endpoint",
    )
    p.add_argument("--node-id", default=socket.gethostname(), help="Logical node identifier")
    p.add_argument("--state-db", default="sync_state.db", help="SQLite execution journal path")
    p.add_argument(
        "--ready-file",
        default="",
        help="Optional file written atomically with bound HTTP/UDP ports after startup",
    )

    p.add_argument(
        "--adapter",
        choices=("sim", "http-chassis", "robonix-lite3"),
        default="sim",
        help="Motion & sensing adapter backend",
    )
    p.add_argument("--chassis-url", default="", help="Base URL for --adapter http-chassis")
    p.add_argument(
        "--container-name", default="robonix_lite3_ros", help="Docker container for robonix-lite3"
    )
    p.add_argument(
        "--mapping-url",
        default="http://127.0.0.1:8092/api/state",
        help="Local mapping state URL for robonix-lite3",
    )
    p.add_argument("--expected-map-id", default="", help="Optional map_id pin for robonix-lite3")

    p.add_argument(
        "--clock-mode",
        choices=("auto", "mock"),
        default="auto",
        help="Clock probe implementation (auto uses chronyc/pmc; mock uses fixed offset)",
    )
    p.add_argument("--mock-clock-offset-ms", type=float, default=0.0, help="Mock clock offset (ms)")
    p.add_argument(
        "--mock-clock-unsynced",
        action="store_true",
        help="Simulate unsynchronized host clock in mock mode",
    )
    p.add_argument(
        "--max-clock-offset-ms",
        type=float,
        default=20.0,
        help="Default choreography wall-clock gate threshold (ms)",
    )

    p.add_argument("--sim-x0", type=float, default=0.0)
    p.add_argument("--sim-y0", type=float, default=0.0)
    p.add_argument("--sim-yaw0", type=float, default=0.0)
    p.add_argument("--sim-speed-scale", type=float, default=1.0)
    p.add_argument("--sim-start-delay-s", type=float, default=0.0)
    p.add_argument("--sim-fault-at-s", type=float, default=None)
    p.add_argument("--sim-lidar-front-min-m", type=float, default=5.0)
    p.add_argument("--log-level", default="INFO")
    return p


def _build_adapter(args: argparse.Namespace) -> RobotMotionAdapter:
    """Instantiate the selected robot motion adapter."""
    if args.adapter == "sim":
        return SimAdapter(
            x0=args.sim_x0,
            y0=args.sim_y0,
            yaw0=args.sim_yaw0,
            speed_scale=args.sim_speed_scale,
            start_delay_s=args.sim_start_delay_s,
            fault_at_s=args.sim_fault_at_s,
            lidar_front_min_m=args.sim_lidar_front_min_m,
        )
    if args.adapter == "http-chassis":
        if not args.chassis_url:
            raise SystemExit("--chassis-url is required when --adapter=http-chassis")
        return HttpChassisAdapter(args.chassis_url)
    return RobonixLite3Adapter(
        container_name=args.container_name,
        mapping_state_url=args.mapping_url,
        expected_map_id=args.expected_map_id,
    )


def _build_clock_probe(args: argparse.Namespace) -> ClockProbe:
    """Instantiate the selected wall-clock synchronization probe."""
    if args.clock_mode == "mock":
        return FixedClockProbe(
            offset_ms=args.mock_clock_offset_ms,
            synchronized=not args.mock_clock_unsynced,
            source="mock-ptp",
        )
    return SystemClockProbe()


def main(argv: list[str] | None = None) -> int:
    """Run the `roboguide_sync` HTTP/UDP daemon until shutdown."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    adapter = _build_adapter(args)
    clock_probe = _build_clock_probe(args)
    journal = SyncJournal(args.state_db)
    hub = UdpHub(bind_host=args.udp_bind, bind_port=args.udp_port)
    service = SyncService(
        adapter=adapter,
        journal=journal,
        hub=hub,
        node_id=args.node_id,
        advertise_ip=args.advertise_ip,
        clock_probe=clock_probe,
        max_clock_offset_ms=args.max_clock_offset_ms,
        http_host=args.host,
        http_port=args.port,
    )

    stop_ev = threading.Event()

    def _on_signal(_signum: int, _frame: object) -> None:
        """Signal the daemon loop to exit."""
        stop_ev.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _on_signal)
            except ValueError:
                pass

    service.start()
    try:
        if args.ready_file:
            rf = Path(args.ready_file)
            rf.parent.mkdir(parents=True, exist_ok=True)
            tmp = rf.with_suffix(rf.suffix + ".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "node_id": args.node_id,
                        "http_port": service.port,
                        "udp_port": service.udp_port,
                        "advertise_endpoint": f"{args.advertise_ip}:{service.udp_port}",
                    }
                ),
                encoding="utf-8",
            )
            os.replace(tmp, rf)

        logging.getLogger("roboguide_sync").info(
            "started node=%s adapter=%s http=http://%s:%d udp=%s:%d",
            args.node_id,
            adapter.adapter_kind,
            args.host,
            service.port,
            args.udp_bind,
            service.udp_port,
        )
        while not stop_ev.is_set():
            stop_ev.wait(timeout=0.5)
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
