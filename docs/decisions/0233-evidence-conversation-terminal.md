# ADR-0233: Evidence conversation and concurrent terminal observation

Status: Accepted; implemented with actual Provider and software-Node acceptance

## Decision

Workbench v0.3 separates evidence conversation from explicit Mission commands. Natural-language
questions may select at most four original HTTP reads and three Provider calls within an original
60-second turn. Composite explain costs three reads. Repeated reads are refused. Model explanations
must cite complete current-turn evidence; original host-rendered facts remain independently visible.
Missing/oversized/error responses stay unavailable. Tools remain closed reads under the current
identity; query data never grants permission to invoke a command or changes MI planning input.
Ordinal identities and implicit comparison cohorts freeze before the first read of each model
batch; subsequent model calls can use newly observed references. Composite diagnostics retain
each subread's availability and cannot be cited as complete when any component is unavailable.
One-shot CLI outcomes distinguish complete success (0) from partial or failed answers (2),
without interpreting rendered error text as status.

Bounded historical messages and current references provide continuity, without making historical
explanations current facts. A v0.2 session upgrades into a separately locked v0.3 copy; original
bytes and all original command identities/pending states remain. Executable draft selections do
not cross process restart. The only foreground worker owns session writes; background watches
use separate read clients and never change ordinal references or stored authority.

The lightweight terminal uses prompt_toolkit asynchronous input and stdout patching. At most three
watchers poll a fixed original Mission every two seconds for 150 observations. Changed results
notify; terminal states stop. Stopping display/watch never cancels Mission execution. Foreground
work drains before closing its session lease, including after the user stops waiting.
Local confirmation selections become usable only after complete terminal output and flush.
Hidden or failed output revokes those selections after the original worker drains; a fresh
`/request` can display the exact current draft again. Shortcut keys cannot select an in-flight
worker's suggestion. HTTP phases share one original monotonic deadline, including response
headers and chunk framing; partial progress never renews it or authorizes a POST retry.
Plain and asynchronous frontends share the same display acknowledgment. Original visible
references remain checkpointed while a result awaits display; hidden/failed results restore
those references after the sole writer drains, preserving actual commands and receipts.
Resolved Mission IDs use typed literal reads rather than round-tripping through command syntax.
Transport framing must be unambiguous and completely terminated; address fallback consumes the
same original budget. Generated v0.3 migration-copy names remain resumable even when the suffix
extends a maximum-length legacy name; new-session identity bounds stay unchanged.

No Controller, Node or Mission Request generation changes. Existing `/plan`, `/run`, `/execute`,
approval and original command reconciliation remain the only command paths.

## Validation

Offline tests cover citations, budgets, repeated queries, partial facts, migration and real asyncio
watch scheduling. Live acceptance separately records actual Provider identity/usage (60-call total
limit across roles), actual software Nodes, independent file verification, and failures. These
observations do not establish robot or physical safety evidence.
