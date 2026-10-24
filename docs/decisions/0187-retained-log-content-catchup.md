# ADR-0187: Content catch-up through exact retained committed Logs

Status: Accepted; implemented stable-membership Log slice with native software acceptance. Complete
A/N1/N3 and SoftwareComplete remain open.

Ordinary `PushCommittedContentV1` keeps its requirement that both members have accepted exactly the
same application. It cannot supply bytes needed before a lagging member applies a historical Log.
New explicit describe/request/push Log commands bind the source and receiver to their independently
read exact retained `LogId` and complete original candidate. The source additionally checks its
durable committed pointer. An absent, changed, truncated or uncommitted source Log refuses; a
receiver needs its actual durable Log and active original Log-producer pins before stream Ready.

The original ContentQuorumTarget is used only as immutable stream retention context in these new
commands. Its revision records the source's frozen observed committed application revision; its
batch/checkpoint/input fields identify the exact historical candidate, not a current accepted view.
Only the new Log commands have this interpretation. Every chunk independently rechecks the original
Log, current exact membership/epoch, actual original member Root owners, TLS and nonrenewing clocks.
The source's revision cannot regress below the frozen observation. Ordinary proposal, committed
route, application Commit and semantic read permissions retain their existing interpretation.

The receiver first persists through OpenRaft's original Log/producers, then requests missing actual
inputs from the authenticated Append sender. A bounded original ingress budget covers discovery and
all requests. It uses existing stream intents/pins/receipts, with a deterministic original ID bound
to source, receiver, Log and complete qualified content. Existing Unknown is observed only;
it cannot generate another receive/source read, ID or clock, including after an epoch change.
The ID excludes epoch; the original admitted target independently freezes its original epoch.
Actual stream Ready still precedes source payload opening. Schema/header/corruption errors never
become missing-content permission.

The original bounded blocking apply wait covers explicit network parent7 as well as parent8.
An actual consensus-only prefix, including bootstrap membership, is committed before waiting on a
following content command. Each original command remains atomic and the entire invocation retains
one original queue clock. Waiting for bytes must not prevent the membership required to authorize
their transfer from becoming durable; no application command is exposed before its own bytes.
Actual byte publication is required before application authority. The network worker never performs
synchronous file hashing or waits for parent SQL. Failed catch-up retains the original apply/stream
fences and does not fabricate completion. No new persistent authority, Node contract or checkpoint
schema is introduced: the exact retained Log, original producer and existing stream journal carry
the authority. Old peers refuse the new wire commands; no proposal staging permission is widened.

This first slice covers stable, already-applied identical membership on source and receiver.
Learner/joint membership catch-up, network Snapshot reception, historical source selection/repair,
full GC/upgrade matrices, cross-host/device continuity and overall completion remain required.
Acceptance includes actual mTLS services, missed publication, subsequent Log replication, original
receipt checks, duplicate/Unknown, wrong Log/owner/uncommitted/absent-source rejection and restart.

Actual evidence and remaining composed fault requirements are recorded in
[the retained-Log acceptance](../development/acceptance/2026-10-24-retained-log-content-catchup.md).
