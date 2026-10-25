# ADR-0214: Explicit Federation inputs for owned replicated Controllers

Status: Accepted for implementation, 2026-10-26.

ADR-0211 implements resource-bearing Federation in the current application. The actual supervisor
clears inherited environment and rejects its three standalone Federation variables. Consequently,
standalone acceptance does not establish a deployable replicated participant or root.

Add optional closed `federation` process inputs with explicit schema
`roboguide.federation-process-inputs/v0.1`, immutable declared domain, authorization path and
optional root peer-profile path. The supervisor validates bounded real JSON inputs and binds their
initial bytes to the common application policy. Each owned child receives exactly those explicit
values after environment clearing. Raw environment variables cannot bypass this boundary.
Federation requires a current supporting application and explicit owned-content plus Node/Operator/
Artifact mTLS inputs. No default domain, peer, Node, resource or device is introduced.

This wires existing v60 application authority; it adds no ledger, permission or budget.
Absent inputs retain exact prior policy identity. Old binaries reject the new closed
field. Existing pinned profiles cannot be edited into another authority: deployment must retain
their normal upgrade or explicit drained source-initialization gates. Restoring SQL does not
start services or install credentials; actual reactivation revalidates explicit original files,
TLS/principals, profile history, epochs, clocks and current all-member content.

Each domain still owns its own Raft group, Control resources and durable local process history.
A routing endpoint is a deployment component, not a source of majority authority. Real TCP/TLS
passthrough must select an actually observed original leader per connection, retain actual failures
and never retry a business mutation. Existing connections are not migrated into another epoch.
Original root replacement fences dispatch and uses the existing complete stop/revoke protocol;
neither infrastructure recovery nor a global Commit decision proves physical continuity.

An unchanged actual configured/committed Federation policy can be observed without acquiring the
application writer. A changed policy still takes the original authorized writer, rereads actual
configuration and commits before publication; every business mutation independently rechecks it.
Outgoing work retains the exact SQL-admitted kernel budget, including intent-Commit queueing.
Before any credential or network I/O it computes only that original remainder. Expired work retains
an explicit failure and cannot receive a fresh timeout. This tightens the existing v60 budget meaning
without changing stored records or granting a new command.

Acceptance composes real independent software domains and their original stores, current pinned
mTLS, partition/reply loss, epoch replacement, complete content and declared cold recovery.
Every actual owned process is reaped and all original Unknown/nonce/budget/goal/decision evidence
survives. Protocol fixtures, single-host observations and software children remain explicitly
separate from devices, external model inference, cross-host results and long-term SLO.

Explicit recovery-profile v0.31 retains every v0.30 Archive, Root, original clock and mandatory-table
requirement while allowing multiple complete independent Replica profiles in one quiescent bundle.
Application authority generations and explicitly declared physical layouts remain independent:
current application metadata may retain original parent8/Root4–7 instead of inventing a Root8
upgrade. Each actual layout keeps its original markers, tables and supported capabilities; parent11
coordinated Archive still requires its actual Root8. Earlier profiles retain their original limits.
Member identity is qualified by its original profile; Root ownership remains globally unique.
Every original committed membership still requires its complete exact Root map. Prior profiles
keep their single-cluster restriction. Active Federation references require their actual declared
domain authorities in the same bundle; released terminal history does not require deleted owners.
All actual writer leases precede any source read. Verify and restore retain the same grouping and
refuse absent/conflicting authority before publishing a destination. Restoring never starts a service.
