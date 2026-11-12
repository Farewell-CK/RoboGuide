# ADR-0231: Read-only conversation workbench

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
