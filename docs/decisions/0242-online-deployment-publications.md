# ADR-0242: Online physical entities and versioned deployment feasibility

Status: Implemented and isolated-process accepted on 2026-11-09; field deployment pending.

Maintenance review fixes preserve the same durable format. Unchanged typed Mission/Group
retries are recognized under the original writer, after current authorization and exact tenant
checks, before new-admission feasibility/verifier/capacity checks. They acknowledge prior
acceptance without SQL, dispatch, clock renewal or a fabricated missing admission receipt.
Changed plans/Groups remain conflicts, including after a runtime revision. POST retains its
acceptance response; current lifecycle remains a GET projection. Published documents compile
once per accepted candidate and share immutable Arc interpretations. Nonserialized caches
rebuild only after complete cold evidence validation; health/inventory do not resolve profiles.

The existing HTTP Controller lacked physical registry publication and froze optional
feasibility at process startup. Use the original global recovery-admin authorization,
FIFO application writer, Control provenance, checkpoint and command journal. No second
gateway, device process, scheduler or execution authority is introduced.

GET/POST /v1/physical-entities uses complete existing physical deployment documents.
An exact expected_revision and next revision are mandatory, with zero for bootstrap.
Control validates entity uniqueness, original registry identity, monotonic provenance
and existing Actor binding consistency before Commit. Original reservations and bindings
remain. Authentication and declarations create no Node, readiness or hardware evidence.
Checkpoint retains digest-only publication history and original command receipts, never
the current entity/Node table. Cold startup must explicitly supply the latest published
physical file. Missing, changed or old current evidence refuses before serving work.
Pending original recovery authorization is revalidated before publishing a changed registry.

GET/POST /v1/deployment-feasibility publishes complete validated existing source documents
under a stable publication registry, expected revision and next version. The current
physical revision is checked inside the same writer. Each source retains its original
World identity, content digest, source semantics and original observations. Publication
does not convert startup/reset evidence into live inventory or renew any measurement.
Online documents cannot automatically enable reset preference or initial assessment
clocks. Those legacy opt-ins continue only under their explicitly supplied startup profile.

New normal Mission admission and Federation Prepare select the current committed profile
inside their original writer. Existing Control restrictions retain the original digest;
runtime revision policies/candidates use their original frozen profile. Every published
source remains bounded retained history. On cold restore the original startup source,
when present, must still match; retained versions reattach each original runtime policy
and Control restriction. Source files are not watched or silently replaced on disk.

New closed RegistryCommandKind values fence prior Control decoders. Current readers
validate the receipt/digest histories and complete feasibility documents, including
required original startup lineage. Unknown/missing evidence cannot be replaced by a
fresh bootstrap. SQL failure publishes neither authority nor memory changes; exact
same-principal command retries return the original result and acceptance clock.
The original AuthorizedRequestGate is checked after obtaining the writer and again after
SQL staging before final Commit, so expired or withdrawn grants cannot publish a partial update.

History is bounded to 128 revisions per authority and 8 MiB retained feasibility bytes.
Exhaustion refuses new publications without discarding running work or history. Routing,
identity, tenant grants, readiness, proposal, Commit, Bind and Runtime remain separate.
Physical trials, production migration and full MI World ingestion remain independent.

See [the acceptance record](../development/online-deployment-publications-acceptance-20261109.md)
for actual mTLS Node execution, original session/ownership retention, SQL/permission rejection,
source/version cold recovery and genuine old-writer refusal.
