# Mission Request v0.5: explicit submission intent

An opted-in request includes mandatory `submission_intent` with `mode`, nullable `draft_revision`
and nullable `draft_digest`. `draft_only` may deliberate but cannot submit unless an explicit
execute command grants the exact current draft. `submit_when_ready` retains the original request's
permission to proceed under the existing Review, risk approval, preflight and Control gates.

Create with `POST /v1/mission-requests` and an original `Idempotency-Key`:

```json
{
  "schema_version": "roboguide.mission-request-create/v0.2",
  "instruction": "Inspect the warehouse",
  "submission_mode": "draft_only"
}
```

A reviewed draft waits at `AwaitingExecution`. `POST /v1/mission-requests/{id}/execute` accepts only
`draft_revision` and `draft_digest`; it grants execution intent, not risk approval. If policy
requires approval, the request waits at the original `AwaitingApproval` gate and `/approve` retains
its approver role. `/execute` requires submitter permission. Every submit rechecks intent, including
retry. Editing the dialogue clears a draft-specific grant; history preserves the old record.

Original `{instruction}` callers keep v0.4 behavior. Old HTTP decoders reject the new create fields,
and old record decoders reject v0.5 on restore. No automatic downgrade to v0.4 is allowed. The
original mode cannot be removed or changed for the same request. No Node or Controller schema changes.

Request cancellation and running-Mission cancellation remain separate. An unknown command outcome
is observed/reconciled using its original HTTP identity; a 404 never permits replacement execution.
See [ADR-0232](../../../docs/decisions/0232-conversation-submission-intent.md).
