# ADR-0143: Compact the local use-lease reference projection

Status: Accepted, 2026-10-21.

## Context

ADR-0142 connected durable Memory-use grants to provider-local content references so local GC could
see the currently authorized content. Its first implementation retained every expired use-lease
generation as an inactive row. The durable UseLeases journal already retains original grants,
revokes, budgets, and command receipts. Keeping duplicate historical projection rows consumed the
same 16,384-row capacity used by explicit local content owners, so normal renewals could eventually
prevent unrelated new references and grants.

## Decision

`content_references` is the current GC projection for `use_lease`, not its history authority. The
UseLeases journal remains authoritative for original command identity, receipts, generation,
membership, and budgets. Only `UseLeases` may create this kind; the generic `/v1/content/references`
route rejects `use_lease` acquire and release requests.

On renewal, one transaction against the shared Memory store removes prior generations for the exact
lease and inserts the complete current generation. If insertion or that transaction fails, the old
projection remains intact. The committed lease remains an independent GC blocker until replay of the
exact original grant repairs the projection. Durable revoke and the provider's restart fence remove
that lease's projection rows; a failed cross-database deletion leaves the old active row blocking GC
until exact command replay retries it. Restart fencing still does not create an original Controller
revoke receipt.

The original command journal continues to control replay: an old grant replay cannot reinsert a
superseded or revoked generation. Explicit `execution`, `lineage`, `replica`, `snapshot`, and `archive`
references retain their release tombstones so delayed acquire cannot reactivate them. Thus recurring
use-lease renewal does not grow this table without bound, while explicit owner history remains
fail-closed under the existing 16,384-row limit.

No SQL schema version changes. Existing v3 rows remain readable. A later synchronization or restart
fence removes inactive legacy use-lease rows; active old-generation rows are removed atomically with
the next successful renewal.

## Validation boundary

Provider tests cover twenty successive real lease generations, retained command receipts, revoked and
restart-fenced projection cleanup, forged external use-lease rejection, and SQLite trigger failures
between the independent lease and content databases. They exercise software authority only and create
no Node, device, model, simulator, or physical safety evidence. Cross-host replica/archive/catalog
coordination and global GC remain open under F4/F5.
