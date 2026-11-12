# ADR-0252: Conversation sessions and explicit Mission submission intent

Status: Accepted; implemented with software validation

## Decision

The second workbench version adds durable local sessions, bounded composed reads, Mission discovery
and watch, and explicit Mission Service commands. Session files are private, bound to configured
origins/tenant/client identity, and retain original outbound command identities before network I/O.
Lost responses are observed under the same idempotency key; reopening never replays commands.

Opted-in Mission Request v0.5 carries a mandatory submission intent. `draft_only` permits
deliberation but holds the reviewed draft at `AwaitingExecution`. `submit_when_ready` permits the
original request to proceed under existing review/approval/Control policy. Legacy v0.4 input keeps
its original behavior; no old request acquires a new grant. Old decoders refuse v0.5 records.

An explicit execute command freezes the exact displayed draft revision/digest under the Request
lock. It grants submission of that draft, not risk approval. Draft modification invalidates that
grant; original history remains. Every actual submit checks the intent again, including retry and
deployment recovery. Restart never renews permissions or clears ambiguous original submission.

Natural-language routing can propose actions, but only explicit `/plan`, `/run`, `/execute`,
`/answer`, `/approve` (or selection of their displayed action) invokes a command. Plain ambiguous
assent never grants execution. User text is forwarded verbatim; the routing model cannot rewrite
the Mission instruction. Read-only queries retain scope and evidence identity. Watch cancellation
stops local observation only. No shell, direct Node operation or new Control authority is added.

## Validation

Verify plan-only create/edit/retry/restart, stale execute refusal, independent approval, original
submission response loss, old decoder refusal, session origin isolation and write-ahead receipts.
Use real HTTP/SQLite with fake model/Controller fixtures and retained software labels. Physical
execution and live model accuracy remain independent acceptance.
