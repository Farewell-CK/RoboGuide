# Original Memory revoke observation v0.1

This is an independent, explicitly authorized readonly observation of an original revoke.
It cannot call control/import, grant permission, renew the original budget, replace a provider,
or overwrite an initial Unknown receipt. Each original revoke permits one session, at most
five minutes and sixteen generations; each query permits one GET per selected provider and
at most thirty seconds of original receiver time. Replays and restart retain all budgets.

`query.schema.json` binds the complete original command digest and sorted distinct original
provider set. `receipt.schema.json` retains exact query identity and per-provider typed evidence.
Confirmed requires an original provider proof; Unknown/NotIssued/Conflict cannot release ownership.
Rust additionally validates canonical digests, original preparation, exact complete coverage,
immutable backend proof and original budgets. Structural schemas alone do not prove those facts.

Node catalog v0.14/config v0.12 negotiates Node Contract v0.11 over Protocol v0.4. Older use
contracts remain immutable. Controller/supervisor wrapper v39, Node journal v11 and event codec
v26 fence earlier writers. See [ADR-0107](../../../docs/decisions/0107-original-revoke-observation-and-provider-progress.md).
