# Spatial file provider

This deployment adapter computes immutable map revisions from actual PLY/PCD or planar occupancy files.
It owns local file geometry, not Control, Mission semantics, map activation or current localization.
No robot, simulator or model is created. It implements CPU FPFH/RANSAC, ICP, pose-graph optimization,
bidirectional overlap/residual checks, sparse voxel tiles and parent-anchored incremental revisions.
The declared anchor is the first source's frame or an exactly verified parent's frame. The separate
planar profile uses bounded correlative SE(2) search, rigid ICP and a fixed-anchor pose graph.

Use an independent Linux Python 3.11 environment with [requirements.txt](requirements.txt). Native
dependencies stay outside the Mission Service environment. Quality gates, work limits,
unit, frame and immutable source hash is explicit in the job; no device/name-specific behavior exists.

```sh
PYTHONPATH=integrations/spatial-provider /absolute/spatial-venv/bin/python \
  -m roboguide_spatial_provider --request /absolute/job.json \
  --source-root /absolute/input --store /absolute/jobs
PYTHONPATH=integrations/spatial-provider /absolute/spatial-venv/bin/python \
  -m roboguide_spatial_provider --observe /absolute/jobs/original-job-id
```

The closed `roboguide.spatial-file-job/v0.1` contract is implemented in `contracts.py`; the actual
software check writes complete requests without supplying a transform to the registration job.
The explicit `roboguide.spatial-occupancy-job/v0.1` contract has its own planar policy; it does not
invent altitude or convert a planar grid into full SE(3). Both jobs use the same CLI/admission/worker.
The [five JSON schemas](../../contracts/spatial/file-provider-v0.1/README.md) describe inputs and maps;
semantic and actual-file admission checks remain authoritative beyond structural JSON validation.
Input paths are root-relative, no-follow regular files. Header allocation, original bytes, decoded
point counts, finiteness, shape, aggregate geometry and process budgets are checked before expensive
work. An immutable intent precedes snapshots/native effects. A separate worker has its original wall,
CPU, virtual memory and file-size limits; its own alarm survives parent death. Exact retries only
observe the original proof, including an unavailable or Unknown result, without renewing any budget.
Original time also binds the actual Linux boot/CLOCK_MONOTONIC domain, before snapshots. Wall-clock
rollback cannot extend work; a reboot or different host cannot start the old job. Completed immutable
timing proof remains readable after reboot. Legacy wall-only evidence stays explicitly historical.

`map.bundle` contains an immutable manifest and content-addressed point-only PLY tiles. The manifest
retains sources, parent revision/hash, estimated transforms, measured errors, explicit policy, version
labels, core native binary hashes and package source digest. Binary hashes identify three core
extension files, not all transitive libraries. Unknown space remains unknown. Registration metrics
and information matrices are not calibrated covariance, and output never proves a Node is localized.
Parent bytes are not rewritten. Corrupt parent/tile metadata, ambiguous/degenerate/disconnected
geometry and exceeded limits produce unavailable/Unknown evidence, never a successful map.

Planar tiles contain sparse explicit cell masks. Unknown/intermediate input values remain unknown.
Free requires full target-cell coverage by one explicit free input square under the estimated
transform and configured margin. Occupied overlap and contradictory free observations retain a
conflict; absent cells never become free. Parent conflicts cannot become unambiguous registration
correspondences. These geometric conditions give no physical navigation or localization assurance.

The implementation follows the primary Open3D [global registration](https://www.open3d.org/docs/0.19.0/tutorial/pipelines/global_registration.html)
and [multiway registration](https://www.open3d.org/docs/0.19.0/tutorial/pipelines/multiway_registration.html)
interfaces. [Current acceptance](../../docs/development/acceptance/2026-10-15-f4-spatial-files.md)
contains actual source attribution, numerical errors, failure cases and reproduction commands.

The private Unix HTTP service runs these same original jobs through the maintained Node HTTP driver.
It listens on a deployment-owned `0600` socket, uses an exclusive backend writer and retains a real
instance UUID plus immutable request/profile/code identity. Exact attempts only observe their original
job; an ambiguous admission cannot launch again. Changed configuration/code makes the old backend
observation-only. Interrupted native work keeps its original deadline across facade restart.

```sh
PYTHONPATH=integrations/spatial-provider /absolute/spatial-venv/bin/python \
  -m roboguide_spatial_provider.http_service --root /absolute/backend \
  --source-root /absolute/input --output-root /absolute/node-cache --config /absolute/profile.json
PYTHONPATH=integrations/spatial-provider /absolute/spatial-venv/bin/python \
  -m roboguide_spatial_provider.node_profile --profile /absolute/profile.json \
  --node-id DEPLOYMENT_NODE_ID --server-endpoint https://CONTROLLER:50051 \
  --artifact-endpoint https://ARTIFACT:8090 --state-root /absolute/node-state \
  --cache-root /absolute/node-cache --socket-path /absolute/backend/local.sock \
  --output /absolute/node.toml
```

HTTPS Node transport requires `ROBOGUIDE_NODE_MTLS_CLIENT_CERT`, `_CLIENT_KEY`, `_SERVER_CA` and
`_DOMAIN`; Artifact HTTPS requires `ROBOGUIDE_ARTIFACT_CLIENT_CERT`, `_KEY` and `_CA`. Controller
enrollment and actual certificate grants must authorize that configured software Node and provider.
See the [identity contract](../../docs/development/identity-lifecycle-v0.1.md). The conformance tool
creates private software test credentials; these establish no hardware identity.

The [local contract](../../contracts/spatial/local-service-v0.1/README.md) fixes routes and profile
fields. Node identity, endpoints, profiles and all source files belong to deployment. The generator
creates no processes, registration or device inventory. Multiple configured slots use the same
canonical `spatial.map.build@v0` and `spatial.map.publish@v0` workflows. Local publication checks
original bundle bytes; Node Service alone freezes prepared bytes, uploads CAS and publishes catalog
metadata before reporting completion. A cancel acceptance remains RUNNING; CANCELLED needs the
actual original native child exit. This is software-process evidence and supplies no Controller stop
certificate or device safe-state observation.

[Process acceptance](../../docs/development/acceptance/2026-10-15-f4-spatial-node.md) exercises actual
computation, formal Node Protocol, CAS, independent verification and restart without another worker.
The first record used trusted-loopback compatibility. The subsequent
[producer authority check](../../docs/development/acceptance/2026-10-15-f4-map-provider-authority.md)
uses actual Node/Operator/Artifact mTLS, Node enrollment, frozen original dispatch declarations and
map v0.2, with config v0.9 / Contract v0.8. It rejects current route/provider/format impersonation and
actual archived binaries. These are same-host software processes, with zero devices or models.
Distributed Group grants/preparation, full typed-map content-policy binding, active-map and GC
remain required F4 work.

The subsequent [prepared consumer acceptance](../../docs/development/acceptance/2026-10-15-f4-typed-memory-replicas.md)
uses explicit full-map Group read/import grants and another actual enrolled software Node. The
receiver imports and computes over real map bytes after preparation/current availability; independent
readers verify the complete source and results. Consumer/Controller/backend restart preserves original
authority and byte replicas without another native worker. Byte placement remains separate from
active-map selection, semantic spatial use and physical localization; those and complete Group
lifetime/handoff/GC remain required.
