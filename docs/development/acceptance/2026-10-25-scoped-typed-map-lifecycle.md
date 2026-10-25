# Scoped typed-map lifecycle software acceptance — 2026-10-25

The complete B module passes actual software-process acceptance on
`codex/nonrelease-remediation-closure`, under [ADR-0208](../../decisions/0208-scoped-typed-map-lifecycle.md).
The maintained paths are documented in [the implementation contract](../scoped-typed-map-lifecycle-v0.1.md).
This does not complete the remaining A1/A2/C–F work or establish SoftwareComplete.

Both maintained native cases use the actual attributed input files declared by
`/data/workspace/roboguide-audit/typed-revision-v55-20261025/profile.json`, the isolated
`/tmp/rg-map-native311-20261025/bin/python`, actual Open3D CPU map computation, two enrolled software
Nodes, actual private provider/worker processes, mTLS HTTP, SQLite and independent file/map checks.
No device, external model, simulator, Habitat, E6 or experiment starts. The actual binaries are:

| Binary | SHA-256 |
| --- | --- |
| integration-server | `326b9eeddadfdb125e9783d924bb646f5c76a6c7f4075120b963f646053ed20e` |
| roboguide-node | `143d39cfad08fb497faae734a92f837134b4c53ead62f5aa4020e9de880ddc52` |

Actual retained results:

| Case | Evidence | Result |
| --- | --- | --- |
| Explicit stopped handoff + scoped-parent revision | `/tmp/rgs57-r1/result.json`, `scoped-lifecycle-result.json` | PASS |
| Original Request + automatic runtime MI generation and independent Review | `/tmp/rgs57-mi-r1/result.json`, `runtime-mi-scoped-result.json` | PASS |
| Complete manual-case backup/verify/new-root restore/retry and old-writer refusal | `/tmp/rgs57-r-cold3/result.json` | PASS |
| Complete MI-case recovery including original Request and runtime worker SQL | `/tmp/rgs57-mi-r-cold2/result.json` | PASS |

Both execution cases preserve a producing Mission distinct from the consuming source and receiving
Mission. Actual source PIDs are independently reaped with complete original Group/attempt generation
coverage; that independent exit and all original provider revokes precede scoped publication. The receiving child retains complete original
typed production, frame, anchor, contract, lineage and bytes. The following scoped-parent revision
retains that same root and uses normal Control admission for its new Group. Three distinct v0.3
preparations cover the first receiver and the two later Tasks; all five imports remain original
history. Every Task's actual current-use lease can select/query the root map, while altered frame and
revoked-source requests fail. Independent file computation and separate original-goal evidence gate
Task/Mission completion. Actual Controller, Node and provider restart does not repeat work.

Both cases exercise actual Controller SQL admission failure, unchanged stop/ownership/allocation
evidence, and explicit recovery of the original failed writer generation before retry. Old
producer-only handoff, altered source frame/scope, old policy and expired input are rejected.
Current workers/use/active references block actual local reclamation. Only original revokes and
actual worker exit permit retiring all five imports, then durable tombstone and actual local unlink.
The original global CAS and Catalog/lineage references remain independently retained.

The MI case creates the original authenticated Request before target execution, then terminates
the actual original child and consumes its backend's native wait. The maintained MI worker performs
original discovery/claims/nonces and scoped declarations, actual generation and independent Review,
SQL interruption, reply loss, read-only reconciliation and old-SQL rollback refusal. Exactly two
calls reach the explicitly labelled local Responses protocol fixture; external model calls are zero.
This is execution/protocol evidence, not an inference-quality measurement.

Cold recovery selects explicit v0.26, original Artifact storage v3, Controller v57, both Node v15
journals and the composite Memory v5 stores. The MI case also retains Mission Request and runtime-MI
SQL. Before copying, all original writer/inode leases are held. Independent reconstruction checks
exact scoped ancestry against original Catalog publication and all currently required CAS bytes.
All pre-existing original rows survive; Snapshot operations/pins are separately accounted. Actual
restored SQL equals the immutable bundled SQL, and deleted local payloads remain absent. Genuine
v56 Controller, v14 Node journal writer and the frozen old Memory package each refuse the new
authority for the expected storage/checkpoint reason, with original authority rows unchanged.

| Bundle | Pinned manifest digest |
| --- | --- |
| Manual | `sha256:bb6486f1d124e7a39be25eb0ebe276252d8f55b31ff32946d63428626f6c32ce` |
| MI | `sha256:0b71d127508bc93692cd23ad57408c9a3cee0bbd633a8e10d59855fd5c22ecdc` |

The final recovery validator also independently re-verifies both immutable bundles after the cycle
and legacy-contract counterexamples were added. Restore starts no service and creates zero new
Nodes; it never renews original budgets or grants current availability, localization or stopping.

Validation passes workspace Rust tests (1,182 passed, 2 ignored), all-target/all-feature Clippy,
private warnings-denied Rustdoc, Rustfmt, Mission tests (602 in the initial whole suite plus the
new DTO regression, with 28 affected parser/provider tests passing), Memory provider tests (88),
recovery tests (238), strict typing including 308 maintained source files, Ruff, function docs and
offline retained-command/schema checks. The actual v56 quota process gate also passes after fixing
the maintained checker's missing v56 allowlist entry, which caused the previous remote CI failure.

Earlier native attempts are retained under `/tmp/rgs57-1`, `-2`, `-3`, `-mi1`, `-4`, `-mi2` and cold
attempt roots. They include the original consuming-Mission refusal, missing scoped data-plane read,
the checker's incorrect HTTP 200 expectation for the existing 202 Commit contract, and the correct
refusal of bootstrap CAS or missing standalone-v0.26 support. The final cases use explicit retained
storage from the original publication; no old evidence is relabelled or fabricated.

Software process exit, file-map consumption, local reclamation and declared-data recovery do not
prove device stopping, physical identity/transfer/localization, hard realtime, cross-host content
continuity or long-term SLO. Those observations remain separate from this completed software module.
