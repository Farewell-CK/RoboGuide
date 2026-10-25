# Original coordinated Archive commands

Global recovery-admin Operator commands, explicit owned process v0.9 / parent11 only. JSON Schema
describes closed public inputs; the implementation additionally bounds command IDs to 128 UTF-8
bytes, requires the actual complete original writer/epoch and checkpoint-covered prefix, and retains
the first actual kernel budget. Caller, tenant, source owner, epoch, clocks and completion are never
accepted as request-body authority.

Duplicate original IDs observe the original record, including across source-owner changes. A new
leader cannot relabel an old source, renew its budget or repeat publication. Changed original wire
inputs or principal refuse. Explicit abandonment reduces rights while preserving original Unknown.
Private complete metadata packages cannot be downloaded or published through ordinary Artifact or
Memory routes. Existing standalone Archive v0.2 retains its original destination/copy meaning.

See ADR-0212, `docs/development/coordinated-content-archive-v0.1.md` and explicit recovery v0.30.
