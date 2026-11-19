# ADR-0258 Periodic Node telemetry persistence

Controller deployments can set `ROBOGUIDE_TELEMETRY_PERSISTENCE_POLICY` to a
JSON file of approved periodic diagnostic schemas. An absent policy preserves
the existing behavior. A conservative example is:

```json
{
  "max_persist_interval_ms": 30000,
  "schemas": {
    "lite3.robot-state/v0.1": ["observed_at_ns", "received_at_ns", "source_stamp_ns", "snapshot_read_at_ns", "age_ms"],
    "roboguide.host-inspection/v0.1": ["observed_at_ns", "received_at_ns", "source_stamp_ns", "snapshot_read_at_ns", "uptime_seconds", "age_ms"],
    "lite3.camera-capture/v0.1": [],
    "roboguide.image-inspection-result/v0.1": []
  }
}
```

The first sample, changed semantics, source epoch, confidence or validity,
and an elapsed persistence interval all retain the exact original record.
The policy ignores only explicitly named clock fields while comparing values.
The live projection still advances on every accepted sample. Checkpoints
retain the last persisted sample with its original receive and source clocks.

Controller decision and Belief source policies keep their original records,
as do World evidence, execution progress, and safe-state observations.
If a new decision registry names a channel previously sampled only in memory,
the next accepted sample supplies its exact evidence. Pending samples only
present in the live projection are unavailable to authority consumers.

Periodic-only ingress uses the existing SQLite and checkpoint checks before
skipping an unchanged batch. The historical event formats and telemetry
body integrity checks continue to apply.
