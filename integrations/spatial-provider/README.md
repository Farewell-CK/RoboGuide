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

Configured Local EAIOS/Node workflow execution, authenticated CAS publication and active-map consumers
remain required work. This CLI is not a completed distributed Spatial system.
