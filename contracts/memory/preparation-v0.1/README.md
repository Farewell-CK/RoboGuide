# Memory preparation and availability v0.1

`preparation.schema.json` binds the complete accepted logical purpose, current physical attempt,
source session, Control policy/membership, canonical invocation/resources and original inputs.
It carries metadata only. Typed-map inputs retain the full immutable raw map manifest beside its
generic discovery projection. The receiver is the configured Local EAIOS owner of the operation.

`control.schema.json` defines Prepare/Probe/Revoke; `receipt.schema.json` separates their outcomes.
`availability.schema.json` describes actual original-provider current bytes. JSON Schema checks
structure; Rust additionally checks canonical digests, exact cross-field identity, complete input
coverage, source contracts, current Control/Runtime authority and original deadlines.

Prepare receipt is durable admission, import Completed is historical backend completion, and
Available is a once-observed current content result. None establishes semantic Task satisfaction
or physical stop. Source absence, original-backend uncertainty and denied/expired grants fence
Execute and retain physical ownership. No new identity is created to retry a side effect.

Node time is receiver-local Linux boot/monotonic time with conservative quantization. Controller
time is its own receive time. Neither is compared with another host's clock. The total window is
at most 120 seconds; each original probe ID reads once, at most 16 probe generations and four
deliveries each. Controller freshness is bounded from the first probe issue, never receipt replay.
Revocation persists before ACK, including when it arrives before Prepare. Import/cache/history
survives revocation for explicit reconciliation and future separately authorized use.

`GET /v1/memory/preparations` exposes bounded committed history to globally delegated readers.
The ordinary tenant check forbids global cross-tenant discovery. Commands remain application-owned
and use current certificate authorization, Control commitment, checkpoint transaction and outbox.

Reproduce the actual software path with `tools/quality/check_memory_preparation_processes.py`.
`check_memory_preparation_upgrade.py` uses archived binaries to prove both journal fences and
original/zero-event Controller fences, retaining actual authority row digests. These checks run
zero devices, models, simulators or experiments; software file evidence is not hardware evidence.
