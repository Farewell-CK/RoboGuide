# Runtime plan revision v0.3

[ADR-0126](../../../docs/decisions/0126-scoped-memory-for-runtime-plan-revision.md) adds explicit
original-owner Memory declarations before a candidate output, provider claim or Review.
Earlier v0.1/v0.2 never acquire this permission. Other v0.2 actions retain their explicit authority
checks; neither a schema version nor a discovery result grants permission to call a provider.

`authorize-memory` requires an actual recovery-admin and the original candidate ticket. Its
`authorization` contains `parent_selector`, `target_group_id`, complete immutable `child`, and
the exact `registry_id` / `registry_revision`. Scope and target must match. The child preserves the
original owner/provider, bytes, payload contract and all production provenance; typed Spatial is
excluded from this generic contract. Review binds the full original declaration digest.

Declaring a target does not create a Group or select a Node. Publication requires the original
complete stop/revoke barrier, current source membership/policy/principal and original budgets.
The new Group uses normal Control and its own Task preparation/import/current-use gates. Each
Artifact chunk still requires the actual current child import; no general CAS alias is granted.

At most 16 declarations per candidate and 512 KiB retained declaration metadata across the
Controller. These are semantic application limits. The closed JSON schema checks shape only;
actual source, TLS, original clock, Review, CAS and ownership checks remain necessary.
Wrapper v45 fences older writers, including zero-event declaration transactions. Node Contract
v0.12/config v0.13/journal v13 and the v28 event codec retain their meaning.

Task verifier `plan_digest` uses the normalized domain plan returned by `/tenant`; revision and
Mission-goal APIs use the published plan digest returned by `/plan-revisions`. These are distinct
existing contracts; substituting one for the other is rejected.
