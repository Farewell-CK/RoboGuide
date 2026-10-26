# Federation peer profile v0.3

See [schema](schema.json) and [ADR-0219](../../../docs/decisions/0219-original-stop-only-federation-observation.md).

v0.3 retains the v0.2 original 100–30,000 ms SQL-publication/peer-I/O request scope and requires
`stop_observation_budget_ms` (1,000–300,000 ms). The original Root admission freezes the exact
profile revision and declaration before any peer command. A mandatory complete `origins` table
preserves older transactions as unavailable; an upgrade cannot lend them new GET authority.

The first stop anchors both the unchanged 30-second effect cleanup and independent declared
GET scope to the same actual kernel sample. After cleanup expires, only `observe-stop` GET may
run. It cannot send probe/prepare/commit/release/abort/stop POST or renew any effect, transaction,
nonce or lost reply. Independent exact peer stop plus released resources can close progress;
original Unknown remains in its original intent. The participant keeps its own original deadline.

The scope has one original expiry and at most sixteen observations per intent. Expiry, changed
kernel or unavailable proof retains uncertainty. Cold recovery preserves original clocks and
mandatory admission coverage; missing declarations refuse. Old closed v0.2 binaries refuse v0.3.
These are software facts only, never device stopping or physical continuity.
