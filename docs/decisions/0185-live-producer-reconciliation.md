# ADR-0185: Preserve live original producers during readonly reconciliation

Status: Accepted; implemented. Actual cold data, second restore and three-leader software acceptance passed; full HA/non-release matrices remain open.

## Problem

The actual Root5 cold-restart gate exposed a follower apply failure during concurrent log catchup:
`original content process incarnation is still present`. Normal post-Commit reconciliation can see
another original producer whose intent is already durable while its worker and parent SQL effect
are still live. Absence of its parent row at this moment cannot authorize retirement. Treating the
correct refusal to invent process-end evidence as a fatal cleanup error stops unrelated Raft apply.

## Decision

Separate an independently observed live original incarnation from unavailable process evidence and
actual original-process absence. During producer reconciliation only, an unpublished producer with
no durable scope close and an actually still-live original incarnation returns its unchanged
Unknown record. It writes no absence observation, resolution, release or receipt, keeps every pin,
and does not renew its original effect clock. After the actual managed scope closes, the existing
independent original-parent observation may retire the abandoned producer. Missing process evidence
and namespace conflicts remain errors; they cannot borrow this live-incarnation result.

Explicit copy/reader process-end operations keep their existing refusal while that incarnation is
present. Completed publication, actual scope closure, actual process absence and device stopping
remain distinct. No SQL/checkpoint/wire generation changes are needed because no new authority or
stored evidence is created. Root4 keeps its original physical-backing contract; Root5 uses its
explicit restore chain for parent provenance.

## Acceptance

Require actual held producer scopes and parent SQL: repeated reconciliation keeps complete original
rows, budget and pins identical; only actual permit Drop permits subsequent original-parent cleanup.
Require the full actual cold-restart/catchup/three-leader/read/Unknown gate. Zero Nodes/devices/models/
simulators; this is software storage liveness, never physical stopping or overall SoftwareComplete.
