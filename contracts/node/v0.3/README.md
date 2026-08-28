# RoboGuide Node Service Contract v0.3

Node configuration v0.3 keeps the Node Protocol v0.2 transport unchanged and adds an
optional, independent Spatial Memory artifact data plane. The `artifacts` section declares a
central HTTP(S) endpoint, a deployment-owned cache root, bounded transfer limits, and static
map input/output bindings.

`connect_timeout_ms` bounds connection establishment and `read_timeout_ms` bounds idle time
between successful response reads. Both are non-zero and participate in the durable execution
specification identity, so a restart cannot resume artifact finalization under silently changed
network behavior.

Input bindings select an explicit `(map_id, revision_id)` and expose only a path below the
configured cache root after manifest, size, and SHA-256 verification. Output bindings select a
preallocated immutable revision, distinct `format_name` / `format_version`, and a
deployment-owned source path. The node freezes and hashes the exact producer bytes before a
`prepare-output` execution completes; a later `publish` execution uploads that frozen copy and
records Catalog publication before it completes.

Each artifact capability fixes exactly one optional `artifact_operation` in deployment-owned node
configuration. Canonical intent carries transport-neutral binding references; an echoed
`artifact_operation` must match the configured value and cannot select or override the operation:

- `prepare-output` exposes the configured output path without publishing;
- `publish` uploads, finalizes, and publishes the configured immutable revision before completion;
- `import` stages verified bytes and records `Staged` then `Imported` evidence;
- `verify` reuses the configured input and records `Verified` only after the localization workflow
  completes.

The v0.3 schema accepts only those four operation values. The compiler rejects an operation under
the v0.2 schema rather than silently enabling artifact behavior. The node never infers input/output
behavior from a slot name, capability name, map ID, or local backend. The canonical `map_id` and
`revision_id` must also equal the selected deployment binding before any file or local workflow
side effect.

For the same reason, `map_id` and `revision_id` in every v0.3 binding use the path-safe ASCII
grammar `[A-Za-z0-9][A-Za-z0-9._:-]*`. This matches the Spatial Memory manifest contract and is
checked during startup compilation, rather than being deferred to an execution attempt.

Map bytes never enter the Node Protocol gRPC messages. The bindings are generic and do not
contain Robonix, ROS, vendor, or Local EAIOS-specific branches. A v0.2 configuration without an
`artifacts` section remains accepted by the Node Service compiler.

If local execution has completed but remote artifact finalization is ambiguous, an exact retry of
the same `execution_id` and immutable execution specification resumes only publication or replica
evidence finalization. It never dispatches the Local EAIOS workflow again. A changed identity or
specification remains a conflict, and automatically choosing that retry still belongs to the
external Control/Orchestration recovery loop.
