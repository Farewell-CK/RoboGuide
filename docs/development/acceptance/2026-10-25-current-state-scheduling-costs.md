# Current State scheduling cost acceptance — 2026-10-25

Source: this commit on `codex/nonrelease-remediation-closure`, following `dbe7c1e7`.
Design and contract: [ADR-0205](../../decisions/0205-current-state-scheduling-costs.md), explicit
[registry v0.2](../../../contracts/state/authority-v0.2/README.md).

## Implemented chain

Deployment-authorized source policy → current Node session/tenant/schema/receive TTL/conflict
admission → explicit integer metric units → normal/recovery candidate ordering → original policy
and State audit. Costs do not alter eligibility or resource ownership. Future reservations retain
original complete assessments and validate original policy history on restore. Missing required
measurements have no numeric cost. GPU available-memory preference is explicit; the old snapshot
helper's reversed GPU contribution is also corrected.

The reference provider's readonly `/v1/scheduling-cost` reads actual POSIX host load and online
logical CPU count. It does not measure worker CPU utilization, queue delay, routes, traffic or GPU
memory; those unsupported metrics remain null. Configured Node State exports feed existing formal
transport; no new Node wire, config generation, device or fabricated inventory is introduced.
The Mission-scoped cost view retains existing tenant/Operator authorization.

## Verification

- All workspace/all-target/all-feature Clippy passed.
- Workspace Rust tests: 1,156 passed, 2 ignored, zero failures.
- Repository Python regression and all recovery-tool tests passed.
- Maintained strict mypy, Ruff and function documentation checks passed.
- Published schemas accepted the actual retained policy command and both actual source records.

The actual process composition ran:

```bash
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-cost-module-native-1 --process-workers --artifact-retention \
  --scheduling-costs --async-workers 1 --old-controller /tmp/rg-cost-old-controller-dbe7
```

`/tmp/rg-cost-module-native-1/result.json` records passed with two enrolled software Nodes,
two actual CPU file jobs, one import, two independent file verifiers, four original cost audit
rows, original policy preservation and actual backend/Node/Controller restarts. The exact
Controller SHA-256 is `033058a66d86d02935783c6ee7f6d7e29ef0007d51e2a679d131806a04e5b27e`.
Source/module/configuration hashes and original SQL are retained in that root. The current cost
view becomes unavailable after actual route/session loss without changing original receive times.

The independent old-binary probe against those same quiescent retained stores is recorded in
`cost-old-controller-receipt.json`: actual PID/exit status, binary hash
`674a34b06c88da5dc1158e83df2dcaca5f3aafbcd8c93e40403df36aaab7ed86`, refusal of the new closed
`schedule` interpretation and exit code 1. No older process served or rewrote that interpretation.
The maintained combined entry also supports that optional original-binary probe.

Offline tests additionally cover alternative candidate selection, larger available GPU memory,
null/stale/session/tenant fences, exact original audit without resource claims, numeric overflow,
policy revision/downgrade, legacy digest preservation and altered historical scores. Synthetic
unit inputs establish no deployed GPU or traffic measurement.

## Independent regression and remaining work

Remote `dbe7c1e7` CI identified an unrelated recovery-validator regression: v54 and v55 were
accidentally combined into one three-element parent identity. The fix admits each original
supported two-element identity independently; seven actual SQLite contract cases check supported
versions and malformed/foreign/future marker refusal. Full remote cold-process acceptance remains
subject to the next CI run; local contract acceptance alone does not establish full HA.

This closes the current source cost mechanism. Divisible allocation, complete time/space
commitments, other local resource enforcement, running coordination, complete Federation and
the final non-release matrix remain required. This run creates zero devices, models or simulators
and proves neither physical stopping nor `SoftwareComplete`.

## Subsequent selected-capacity protection

The following Control change also rechecks exact selected IDs/kinds/units at Bind, stopped recovery
Rebind and dispatch readiness. An adequate unselected resource cannot lend capacity. Registration
updates preserve the retained original quota demand; after process restore the same demand is
checked even without a previous live Node snapshot. Unaccounted legacy updates conservatively
retain the previously observed capacity rather than guessing units. Refusal changes no owner,
quota claim, pending recovery commitment or event evidence. This does not introduce divisible
allocation or reinterpret existing minimum-capacity semantics.


The selected-capacity follow-up passed 178 Control, 282 integration-server and 78 Orchestration
checks (two existing Control ignores). Five new cases cover live update/refused Bind/current
readiness/restore/recovery Rebind and unchanged owners/quotas/events. Existing continuation tests
now first prove registration refusal, then explicitly inject an offline reader counterexample
for the independent downstream gate; these synthetic inputs are never deployment facts.

The rebuilt Controller SHA-256 is
`674f98531e2366f645abaa214eb7c2558e1b88ae8a854ab726c15fe29ae5bb28`.
`/tmp/rg-selected-capacity-legacy-1/result.json` passed the complete actual file/cost/restart path
and optional old-process refusal. Its actual original unaccounted Catalog then passed
`check_catalog_adoption_processes.py --from /tmp/rg-selected-capacity-legacy-1 --output
/tmp/rg-selected-capacity-adoption-1`: explicit adoption, original SQL rollback, complete Archive,
actual restart and cold backup/verify/restore/retry, with zero new Nodes/devices/models/simulators.

Remote `9d9c3c0b` passed the earlier three-member cold-recovery step and exposed an Archive reader
that stopped at codec v31. Recovery now accepts v33 only for the four exact Memory replica
variants, requiring the original Task/attempt provenance; missing or altered-shaped provenance
and unsupported future codecs remain refused. Original archive hashes, operation bounds and
profile gates are retained. Four new parser cases plus the existing archive/adoption cases and
the actual composition above passed; the complete next remote run remains separate evidence.
