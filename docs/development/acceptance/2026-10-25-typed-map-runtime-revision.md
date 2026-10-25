# Typed-map runtime revision acceptance — 2026-10-25

Source: `80a274c5` plus the exact-parent and retained stop/revoke checks committed with this record.
Branch: `codex/nonrelease-remediation-closure`. This run rebuilt both runnable binaries before
freezing their identities; earlier diagnostic runs do not substitute for this source observation.

The actual maintained command was:

```bash
uv run python tools/quality/check_spatial_node_processes.py \
  --python /tmp/rg-map-native311-20261025/bin/python \
  --profile /data/workspace/roboguide-audit/typed-revision-v55-20261025/profile.json \
  --source-root /data/workspace/roboguide-audit/typed-revision-v55-20261025/inputs \
  --output /tmp/rgt55-r14 --mtls --memory-consumer --typed-revision revision \
  --old-controller /data/workspace/roboguide-audit/typed-revision-v55-20261025/integration-server-v54
```

`/tmp/rgt55-r14/result.json` reports passed. Its binary hashes, source/module hashes, input
manifest, configuration identity, actual SQL and all owned process exits are retained in that
root. Private TLS keys stay local. The isolated Python 3.11/Open3D environment consumes three
attributed original Open3D files; it starts one actual CPU map worker, two software Node
processes and independent file verifiers, with zero devices, models or simulators.

The chain covers actual map publication, exact source policy, prepare/import/current use,
active-map selection/query, original receiver restart, independent original PID exit and
provider revoke, explicit v0.4 typed declaration, independent Review, normal new Group Control,
two distinct Task/attempt imports, independent Task and original-goal verification, Controller/
backend/Node restart and refusal by the actual v54 binary. Original production and consuming
Missions remain different immutable identities. The parent CAS exception requires the complete
original manifest and original stop/revoke evidence; equal bytes under another owner or scope do
not lend this permission. The remote Clippy failure in `80a274c5` is separately corrected here.

This closes this typed revision composition. Generic Group handoff and generic automatic MI
composition retain their separate actual-process records in `/tmp/rgt55-handoff-3` and
`/tmp/rgt55-mi-1`. The complete typed lifecycle/GC/recovery/fault matrix, remaining resource,
coordination, Federation and final non-release matrix remain required. No software process or
map record proves localization, device stopping, physical continuity or `SoftwareComplete`.
