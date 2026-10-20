# Original-provider content reclamation v0.1

[ADR-0112](../../../docs/decisions/0112-original-provider-content-reclamation.md) defines this local
copy lifecycle. The [closed command schema](command.schema.json) is consumed by the shipped private
Unix Memory backend with `--enable-content-reclamation --enable-use-leases --enable-file-jobs`.
TCP requests cannot activate these routes. The actual backend instance, configured size limit and
complete original import/revocation coverage are runtime checks beyond the structural schema.

| Route | Meaning |
| --- | --- |
| POST `/v1/content/tombstone` | Freeze the original command and imports; actual references/revokes gate tombstone |
| POST `/v1/content/collect` | Recheck the original tombstone, inode, full hash and references; unlink/fsync, then receipt |
| GET `/v1/content/reclamation` | Read original progress using `{ "command_id": "..." }`; never unlink or renew |

`blocked`, `requested`, `tombstoned` and `deleted` remain distinct. Final SQL failure after unlink
leaves `tombstoned`; only an explicit retry of that same immutable command can reconcile it.
Completed import history survives collection and does not grant current availability or reimport.
Every receipt declares `local_copy_only=true`. Global CAS, remote provider and archive deletion
have their own authority. Legacy Active Map GC booleans always return blocked.

Real TLS/Node/CPU/SQL/Unix/restart conformance runs:

```bash
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-content-reclamation-owned --content-reclamation
```
