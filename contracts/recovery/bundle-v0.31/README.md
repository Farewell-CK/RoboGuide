# Complete independent-domain recovery v0.31

`roboguide.recovery-profile/v0.31` preserves the complete v0.30 SQL/CAS/Archive contract and all
original clocks, Unknown, mandatory journals and writer leases. It permits multiple explicitly
declared complete Replica profiles in the same quiescent bundle. Member IDs are qualified by the
original profile; each committed membership must still contain exactly its own original Root map,
and Root ownership is globally unique. Older profiles retain their single-cluster restriction.
Application and storage generations are independent: current application authority may retain
declared parent8/Root4–7 with their exact original capabilities. Source initialization and parent11
coordinated Archive still require actual Root8; the tool never upgrades a layout implicitly.

Active original Federation preparations, Commit/release receipts and root nonces must resolve to
their actual original domains in the same bundle. Complete released terminal history does not
require deleted remote authorities. A label, file hash or caller `passed` flag cannot substitute
for the original actual stored references. Missing or conflicting references refuse publication.

All actual source writer/inode leases precede source reads. Restore writes new data roots, retains
original identities and explicit physical backing receipts, and starts no service or execution.
TLS credentials remain explicit deployment inputs at their declared mount locations. Reactivation
revalidates actual credentials, current permissions/epochs, original budgets and complete content.

See [ADR-0214](../../../docs/decisions/0214-replicated-federation-deployment.md). This establishes
declared software data recovery only, never device stopping, physical continuity, cross-host
performance or long-term SLO.
