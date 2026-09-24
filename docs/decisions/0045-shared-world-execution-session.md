# ADR-0045: Accepted-plan execution session for shared-world topology

- Status: Proposed for review
- Date: 2026-09-24

## Context

An independent Mission with two navigation Tasks may use one logical Actor and
one Physical Entity in sequence. Control correctly defers the second Task while
the first holds its exclusive `space:1` resource. The previous Habitat shared-world
adapter nevertheless waited for a second distinct endpoint before its only reset,
so the first Task could never complete and release that resource. Adding a
distinct-executor requirement in MI would misstate the benchmark goal.

The Local EAIOS sees only one canonical Execute at a time. It cannot infer
accepted Mission topology from the current goal predicate count, a Node
registration, or the next assignment's absence.

## Decision

1. Controller derives `roboguide.execution-session/v0.1` from the accepted
   v0.8 MissionPlan and its Mission-level Group. The descriptor carries exact
   Task/Role/Actor slots, Task DAG dependencies, an independent-mode flag, and
   a canonical SHA-256 digest. It contains no Node, PhysicalEntityId, ResourceId,
   simulator goal, or placement choice. A digest detects accidental mutation;
   authority remains the trusted accepted plan and Control dispatch, not the
   unkeyed digest by itself.
2. For a selected operation whose registered Local System explicitly opts in,
   the descriptor is persisted with the Runtime command and transported in
   additive Node Protocol v0.4 `Execute.execution_session_json`. Other Node
   deployments keep their existing commands without this optional field. The
   shared-world deployment advertises support through registration-local metadata
   `roboguide.execution-session=roboguide.execution-session/v0.1`; the Router
   rejects a nonempty descriptor before sending it to a Node without that
   declaration. Node validates schema, mission/group/slot, digest, canonical
   ordering, and prerequisites before creating the durable local invocation.
   The Local EAIOS independently validates the same identity and schema. Legacy
   commands without the field retain their existing path; deployment rollout
   requires updated Controller, Node configuration, and Node binaries together.
3. The Habitat shared-world adapter admits only its explicitly supported
   topologies: two independent Actors on distinct configured endpoints, or
   one independent Actor whose successive Tasks reuse its assigned endpoint.
   Unsupported coordination topologies fail before reset. The descriptor
   does not make a Task ready or authorize resource reuse: Control alone
   commits, releases, and dispatches each Task. A follow-on assignment must
   have the same Group/session digest and endpoint and satisfy declared DAG
   prerequisites.
4. Serial Task segments use the original EMOS Stage2 policy with a new
   Control-committed AgentArguments assignment for each segment. They retain
   one Habitat world and reset, carry the prior observation into the next
   segment, and preserve continuous simulator step, selected-tool audit, and
   physical diagnostics evidence. Each segment publishes its own local
   terminal fact; only Habitat's official `pddl_success` supplies benchmark
   truth. Missing next assignment reaches an explicit bounded INCOMPLETE
   start-admission state, never synthetic Task success.

## Consequences and limits

One Actor is not forced to use two physical executors, and one endpoint need
not wait for an assignment that Control cannot send until resource release.
The adapter still does not support general multi-Actor handoffs or arbitrary
execution relations. Historical B2 commands without metadata retain the
two-endpoint barrier. The shared-world rollout requires updated Node
configuration and binaries; a registered owner that advertises the session
schema cannot silently downgrade on a stale route, because the Router rejects
the nonempty descriptor before sending it. An unadvertised legacy deployment
retains its existing behavior and cannot claim serial support. A local skill completion remains separate from
Task satisfaction, Mission outcome, and official Habitat success.
