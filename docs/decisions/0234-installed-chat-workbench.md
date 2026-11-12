# ADR-0234: Installed chat configuration and explicit local operator workflow

Status: Accepted; implemented with installed-wheel, pseudo-terminal and native software cancellation acceptance

## Decision

The v0.4 workbench uses standalone dialogue-model configuration independent of MI prompts and
contract assets. Explicit command options override an explicitly selected profile and then user
global configuration. Project-owned configuration/model files require explicit `--profile` or
`--config` selection; changing the working directory cannot change a credential destination.
First-run interactive setup and `roboguide init` preserve explicit connection/identity inputs and write private settings
without persisting API keys; existing environment credential loading and remote TLS rules remain.
Post-publication failures clean up only exact newly owned file identities or preserve a consistent
published pair; preexisting and concurrent replacement files are never cleanup targets.
Installed sessions use the user state directory when no project profile is selected.

The terminal shows local connection/identity/model/session/task context without claiming actual
backend readiness. Scope-bound session listing and new/resume navigation retain exclusive locks
and original files. An unresolved original command blocks navigation; switching clears all local
execution/cancellation selections, and active foreground work and watchers drain before lease
transfer. The owning frontend Store remains the lease owner across navigation.

`/check` sends only the exact displayed plan's production bytes to existing contract-validation
and initial-support assessment ports. It validates original identity/digests, distinguishes typed
blocked feedback from unavailable/malformed sources, and grants no admission or execution scope.

Explicit `/cancel request ID` and `/cancel mission ID` freeze and display separate target scopes;
`/cancel confirm` rechecks original current identity and state before one durable command. The
original Mission Service handles preexecution cancellation; accepted/running Missions use the
Controller's existing normal cancellation authority. Display failure or hidden output confers no
cancellation selection. Ctrl-C retains its existing local-display meaning.

A Controller cancellation journals its exact original source, bytes, target, Group and local
operation identity before POST. This does not invent a server idempotency API. Response loss or
refusal remains unknown and cannot authorize replay. Readonly observation may resolve the local
pending barrier only from exact original Mission/Group Cancelled or Completed evidence; the frozen
resolution witness retains original HTTP Unknown and does not relabel normal completion as cancel
success. Other unavailable/failed/running observations retain the fence. An accepted cancellation,
Cancelling and a software terminal remain distinct; no physical
stopping assertion follows from a software response. The selected Controller's current path decoder
must support an exact literal ID; unsupported encoded cancellation IDs refuse before effects.

`/feedback` creates a bounded private local ZIP from current session logs, receipts, observations
and runtime projections. Known/supported credential forms are redacted without changing original
records, including supported credentials inside bounded serialized JSON/log layers. Every new UI
command invalidates earlier natural-language proposals except its matching explicit `/choose`;
old shortcut choices cannot survive a cancellation preview or another command's result.
The manifest declares limits, omissions and hashes. No email, upload, arbitrary directory
scan, remote log collection or implicit model call is added.

No Controller, Node, canonical Plan, Mission Request or recovery authority is added or upgraded.
Execution still requires original exact draft permission, independent risk approval and Control
admission; session and UI convenience never supply those permissions.

## Validation

Targeted tests exercise actual local HTTP/SQLite, private stores, setup from different working
directories, service-scoped command receipts, failed display confirmation, and model fixtures.
Native software cancellation and pseudo-terminal acceptance retain independent evidence and failed
attempts. Real hardware stopping and long-term service behavior are separate evidence.
