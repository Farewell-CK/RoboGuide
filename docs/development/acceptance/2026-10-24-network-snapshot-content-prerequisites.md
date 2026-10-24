# Network Snapshot content prerequisites acceptance

ADR-0188 is implemented on the single software host with real independent processes, files, SQLite
roots and mutual TLS. The test profile uses arbitrary declared member IDs and creates no Node,
device, model, simulator or physical-continuity fact.

The acceptance command is:

```text
uv run python tools/quality/check_network_content_processes.py \
  --probe <frozen content-network-probe> \
  --storage-probe <frozen content-stream-probe> \
  --source <complete original source evidence> \
  --members <three declared IDs> --snapshot-catchup
```

The passing evidence includes:

- an explicit drained Root4 to Root6 upgrade with `snapshot_receive_ids` and `backing_mode`;
- a source Snapshot after the original applied Log was purged;
- a receiver that first records a durable Snapshot receive issue, then receives exact CAS bytes over
  authenticated mTLS, and only then accepts OpenRaft metadata;
- an actual source-member/Root/TLS/header/input/budget fence, exact byte hashes and an independent
  Root6 retirement proof;
- a real leader process kill, election, restart with the same SQL inodes and complete content
  majority recovery;
- preservation of incomplete/Unknown receive state, old Root4/Root5 entry-point refusal, and
  recovery-tool inventory rejection for missing heads, pins, receipts, source identity or backing
  declarations.

This is software content continuity evidence. It does not establish cross-host independence,
hardware identity, device stopping, physical continuity, long-term SLO or SoftwareComplete.
