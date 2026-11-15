# ADR-0251: Read-only conversation workbench

Status: Accepted; implemented with isolated software validation

## Context

Operators need a conversational entry point for visible Nodes, capabilities and Mission progress.
Existing inventory and Mission GET endpoints already own these projections. Mission Intelligence
generates candidate plans and must not acquire live inventory-based assignment authority.

## Decision

Add an independently named Python conversation package and a `roboguide` terminal entry point.
The initial workbench only queries cluster inventory, one Node, matching advertised capabilities,
and an exact Mission with its Task states. It connects to one explicitly configured Controller;
the result is scoped to the authenticated identity and is not an aggregate Federation inventory.

A bounded Responses request selects a closed query descriptor. The host validates that descriptor,
resolves references against previously displayed IDs, performs a fresh GET and renders facts and
counts itself. Model output is not evidence. No Controller data is interpreted as instructions.
The model receives only the user question and bounded reference IDs, not raw cluster responses.
Explicit commands work without a model. Neither route invokes Mission admission, shell execution,
arbitrary URLs, Node operations or automatic retries.

The client retains fixed-origin transport, original tenant/mTLS authorization, redirect refusal,
response bounds and timeouts. Node projections retain health and liveness timestamps. Registration,
reported availability, observed reachability and Control eligibility remain distinct. No freshness
policy or physical state is invented. Missing data or failed reads remain unavailable.

Sessions retain bounded process-local reference context and the last raw query only; they create
no durable authority or new server contract. Future task execution must be separately implemented
through the existing Mission/approval/Control chain. No storage/schema/Node/physical changes.

## Validation

Use deterministic routing fixtures plus actual local HTTP and CLI boundaries to verify counting,
fresh follow-ups, original displayed ordering, malformed responses, tenant forwarding, redirect
refusal, bounded I/O and absence of writes. Real Provider and deployed cluster acceptance remain
separate from fake-transport and fixture-server checks.

## Generic State read extension · 2026-11-15

The conversation workbench discovers the existing tenant-visible State export catalog and joins
exact registered object/source/channel/schema evidence to current reported records. Host-owned
versioned field profiles interpret battery, pose and posture initially; additional maintained
profiles reuse the same discovery/read tools. Unknown schemas remain metadata, never executable
decoders or inferred physical facts. No server or Node contract changes are required.

State reads retain the original four-GET turn budget, exact source ownership, outer and nested
sample validity, independent liveness and display ACK. The host rechecks original sample expiry
after Provider latency and renders the actual qualified values itself. Conflicts and unavailable
fields remain explicit; no source or model output acquires action/physical authority.
See [State workbench v0.1](../development/conversation-workbench-state-v0.1.md).
