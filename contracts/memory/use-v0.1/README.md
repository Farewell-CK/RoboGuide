# Current Memory-use permission v0.1

This metadata contract is distinct from original import completion and current availability.
`MemoryUseControl` binds the complete original preparation digest, attempt, tenant, Node/session,
configuration, current policy and membership. Grants have contiguous one-based generations and
an original receiver interval of at most 30 seconds. Revoke uses generation/budget zero and is an
irreversible fence, including when it arrives first. Duplicate commands never acquire more time.

All selected original providers must durably acknowledge the interval before Group pending Execute.
The Controller rechecks current authority before issue, delivery and admission, retains bounded
original receipts, and fences outstanding permission on restart. Expiry, ambiguity, session loss
and revocation retain physical ownership behind the existing stop/hold boundary. A content-use
receipt is never physical stop, safe-state, localization or semantic Task satisfaction evidence.

Controller wrapper v38 distinguishes local completion, per-Task release and whole-Group release.
A normally completed Task waits for its own original context and all provider revoke ACKs while
independent live peers keep their permission. Group cancellation, final failure/negative verifier
and recovery apply the corresponding complete receiver set. Revoke provider identities remain
pinned to the original backend instance/contract even though revoke carries no import IDs.
Each Execute send rechecks all pending Group inputs and their earliest original preparation,
availability and use expiry. These application gates do not change the Node wire envelope.

Node Contract v0.10/config v0.11/catalog v0.13 explicitly negotiates this extension. Original
[preparation v0.2](../preparation-v0.2/README.md) requires an explicit `use_contract`. Historical
v0.1 preparation remains readable with original absence; it cannot acquire a runtime use lease.
No historical public schema is broadened to hide the new authority.

The shipped Local EAIOS profile `roboguide.memory-use/shared-kernel/v0.1` requires one actual Linux
kernel/time namespace and a same-user private Unix endpoint. The Node's original boot, namespace
and uptime budget is echoed exactly and checked by the backend, including at bounded content-read
boundaries. Backend startup fences its old permissions and preserves original receipts/budgets.
Core does not compare independent host clocks or interpret vendor-specific receipt fields.

The JSON schemas check closed envelopes and scalar bounds. Rust additionally validates derived
identities, exact original contexts, unique complete provider/input coverage, canonical receipt
digests and byte bounds. The Node adapter validates the negotiated original backend/profile.
Types and unit metadata alone do not establish distributed conformance or device evidence.
