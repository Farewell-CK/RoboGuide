# `sync-provider` (`roboguide_sync`)

EAIOS-neutral provider implementing multi-robot synchronized motion and large-scale clock-triggered choreography outside `core/*` and `apps/*`.

It pairs with RoboGuide's other EAIOS-neutral providers (`spatial-provider`, `memory-provider`) while isolating concrete chassis runtimes behind the `RobotMotionAdapter` interface (`sim`, `http-chassis`, `robonix-lite3`).

## Supported Operations

1. **`mobility.sync-follow@v1`** (`TightlyCoupledCooperation` + `PeerChannel`):
   - Accepts parameterized paths (`roboguide.sync-track/v1`: `line`, `arc`, `turn_in_place`, `hold`).
   - Runs a 20 Hz closed-loop distributed progress consensus controller over sparse peer neighbor graphs (`roboguide.sync-phase/v1` over UDP).
   - Provides N-party transitive gossip barriers, dual-tier peer link watchdog (Tier-1 `400 ms` hold + propagation, Tier-2 fence), and `< 50 ms` TTL `ESTOP` flooding.
   - Hosts `/v1/peer-channels` natively (replacing standalone `peer_link_daemon.py` / manual SSH) and automatically silences on terminal completion (`SUCCEEDED`).
   - **RoboGuide Control Plane note**: `requires-active(source -> target)` becomes `Violated` if `source` completes before `target`. Declare star `requires-active` relations from `leader -> each follower` and set `"done_hold_s": 1.2` on the leader (`sync_params_json`) so followers settle `Completed` deterministically before the leader exits `DONE`.

2. **`choreography.play@v1`** (`Independent`):
   - Verifies local Chrony/PTP clock offset against `max_clock_offset_ms` (Fail-Closed gate) and optional `pack_sha256` before arming.
   - Triggers time-indexed keyframe playback (`roboguide.choreography/v1`) at shared UTC `t0_utc_ms` with zero runtime peer coupling.
   - Isolates any single-robot hardware/obstacle fault to local stop (`FAILED` + `safe_state=true`) without affecting peers.
   - **RoboGuide Control Plane note**: Within a single `MissionPlan`, `core/orchestration` applies Mission-wide SafetyStop atomicity on any task failure. To achieve cluster-level fault isolation with zero `core/*` modifications, submit one `Independent` Mission per isolated performer/subgroup sharing the same `t0_utc_ms` (and assign each Node a globally unique `[[resources]] id` such as `<node-id>-sync-chassis-slot` in `node-config/v0.14`).

## HTTP Workflow Surface

Default bind: `http://127.0.0.1:18210`, UDP bind: `0.0.0.0:18201` (zero external runtime dependencies; pure Python 3 standard library).

| Route | Purpose |
| --- | --- |
| `GET /v1/sync/health` | Service health (`state: "ONLINE"`), bound ports, pose, sensors, clock |
| `GET /v1/sync/readiness` | Readiness (`?operation=...`), aliases `/readiness/sync-follow`, `/readiness/choreography` |
| `GET /v1/sync/endpoint` | Advertised UDP `peer_endpoint` (`roboguide.sync-endpoint/v1`) for `[[state_exports]]` |
| `GET /v1/peer-channels` | Live `channels` array for `[[peer_channel_observers]]` |
| `POST /DELETE /v1/peer-channels/sessions` | Pre-dispatch PeerChannel session arming and cleanup |
| `POST /v1/sync/executions` | Idempotent execution admission (`attempt_id`, `invocation`) |
| `POST /v1/sync/executions/status` | Execution status polling (`PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/UNKNOWN`) |
| `POST /v1/sync/executions/cancel` | Cancel + immediate local `emergency_stop()` + UDP `ESTOP` flood |
| `GET /v1/sync/executions/safe-state` | `roboguide.safe-state-observation/v0.2` evidence feed |

## Adapters (Decoupled from Any Specific EAIOS)

- `--adapter sim`: Pure-software kinematic simulator (`SimAdapter`) for multi-process L0/L1 testing.
- `--adapter http-chassis --chassis-url http://...`: Generic HTTP JSON chassis backend (`/pose`, `/cmd_vel`, `/estop`) usable on ROS2 / Nav2 / Gazebo / Unitree / quadruped / wheeled / humanoid bridges without Robonix.
- `--adapter robonix-lite3`: Reference Lite3 ROS2/Docker bridge (`MapOdomFuser`, `/cmd_vel` slew rate limiter, rear-weight turn compensation, `< 0.55 m` LiDAR fence, `1.8 s` settle rule).

## Quick Verification (L0 + L1 Simulation)

```bash
cd integrations/sync-provider
python -m pytest -v
```

See [deploy/node.sync-addon.toml](deploy/node.sync-addon.toml), [deploy/sync-capability-catalog.json](deploy/sync-capability-catalog.json), [deploy/example-sync-follow-mission.json](deploy/example-sync-follow-mission.json), and [deploy/example-choreography-mission.json](deploy/example-choreography-mission.json) for declarative RoboGuide integration.
