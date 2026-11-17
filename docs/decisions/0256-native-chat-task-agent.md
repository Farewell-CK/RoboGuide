# ADR-0256: Native Chat task tools and recoverable conversation

Status: accepted for implementation, 2026-11-16.

The user wants Chat to pursue an explicit natural-language goal through discovery, clarification,
MI and progress reads. The former default only proposed `/choose` commands and therefore required
another mechanical confirmation even for an already explicit task request. Real-use evidence at
`c17d07f9` also found a compound query losing its independent Mission read after a reference refusal
and repeated directory call. Passing synthetic tests did not establish real-model usability.

## Decision

The default native model receives closed task tools: `clarify_task`, `start_task`, `answer_request`, `read_request`,
`execute_plan`, `approve_plan`, `cancel_task` and `observe_operation`, alongside existing reads.
`start_task` selects a complete host-owned original dialogue suffix, never model-authored task
parameters or a MissionPlan. Explicit planning creates a normal draft-only Request; explicit task
execution uses normal submit-when-ready intent. No `/choose` step is added to that path. The old
proposal descriptor remains passive compatibility and is not advertised to the default task model.

Original MissionActions command IDs, durable before-I/O publication, unknown outcomes and exact
scope remain. One task mutation is allowed per turn; its same-ID duplicate only observes its
original result. Unknown operations may be observed but never replayed. Subsequent read tools can
use actual newly returned Request/Mission IDs. Plan execution and approval require the original
acknowledged unchanged draft plus explicit current user authorization; server-side approval,
permissions, Review, Catalog, resources and Control admission remain independent. A fresh in-turn
plan read cannot manufacture display acknowledgement. Ordinary thanks and discussion-only requests
do not start task effects. This changes Chat's intent ingress, not physical execution authority.

Starting a task first inspects current inventory and displays declared contracts and scoped
capability gaps. This is advisory, not eligibility or a substitute for MI/Control checks. Missing
navigation/task-delegation or map contracts cannot be invented by stronger model reasoning.

Repeated queries return structured cached results and allow independent siblings to continue.
Host prerequisite refusals perform no I/O and consume no HTTP budget. Unresolved parts stay visible;
errors after network admission retain conservative cost and original unknown outcomes. Defaults
are 12 HTTP slots, 8 Provider calls and one original 120-second turn deadline; explicit profile
limits remain unchanged, with bounded maximums of 32/16/300 seconds. There is no unbounded agent loop.

Device aliases resolve only against the actual tenant-visible export catalog. Duplicate formats
remain separate and produce working numbered object selectors; no preferred source or shared frame
is inferred. Complete bounded `/raw` history persists under the original display ACK and scope,
without renewing freshness or adding raw history to model context.

Responses streaming uses the existing origin/TLS/framing/deadline/2-MiB transport, without retries.
Only public text deltas are previews; completed responses alone authorize tools. Incomplete,
conflicting or changed completion evidence refuses. State and task lifecycle values retain host
rendering; previews never create confirmation, selection or displayed-history authority.
`--no-stream` provides explicit compatibility without automatic replay.

### Host-owned pending tasks and in-turn references (2026-11-17)

`task_context.py` owns a bounded, versioned local pending dialogue. Original user turns are
persisted before Provider I/O; only acknowledged assistant context may join them. Clarification
uses the zero-I/O `clarify_task` tool. Direct clarification replies remain compatible. The context
survives normal history-window truncation and resume; model `start_turn` is a source hint and
cannot shorten an existing task or discard its restrictions. A complete explicit new user goal
opens a new context; successful task publication closes the pending dialogue. Incomplete,
corrupt or oversized originals refuse task effects rather than become an empty goal.

The model still interprets goals and chooses tools. The host separately retains submission
limits over the entire original user chain, conservatively treating negated, conditional and
question clauses as insufficient to release an earlier limit. This is a bounded defensive check,
not a claim to solve every natural-language authorization expression. Unclear changes require
natural clarification. Original server submission intent and acknowledged-plan gates remain.

Successful read-adapter projections can supply exact Mission IDs to the same turn's task tools;
partial cohorts and model-authored IDs cannot. An explicit user-named Request may be freshly read
and amended in one turn. These paths do not manufacture execution/approval display ACK, change
tenant scope or skip the normal cancellation preview and current permission checks.

Logical work identity is separate from tool-parameter caching. Correcting a start from run to
plan resolves only that pending task's refusal; unrelated failed queries remain unavailable.
Repeated starts for the same original work reuse the first receipt even when model mode/source
hints change. Actual network errors and original Unknown never gain a replacement command.
Local clarification is not a task mutation and consumes no HTTP slot; all call/deadline bounds
and the one actual task mutation per turn remain unchanged.

### Terminal readability and exact display reuse (2026-11-17)

`plan_view.py` renders every canonical plan field as readable YAML, with exact JSON escaping
when invisible formatting would otherwise change a displayed scalar. A short step overview is
only a reading guide. New, revised and first-resumed plans are still shown completely; no model
summary replaces the full plan or its parameters. Display limits also apply to the actual render.

An ephemeral `acknowledged_draft` is adopted only after the original DisplayTurn flush and
checkpoint succeed. Only the same Request, revision, full digest and whole plan may reuse that
complete display for a compact progress card. Current constraints, issues and approval reasons
remain visible. Failure, interruption, reset and restart revoke this reuse. Existing fresh server
version/permission checks still precede execution or approval. `show_plan` is a zero-I/O local
tool for natural parameter expansion, not a new observation or execution grant.

Busy `/help` and `/status` inspect local frontend metadata without another foreground owner,
history write or Controller query. Queue previews use terminal cell widths and never change the
saved original. Occasional waiting notices appear at 15 and 45 seconds, without timer redraws,
invented percentages or repeated requests. Reply headings distinguish partial/unavailable query
results from Mission or device failure. All output still uses the original complete display ACK.

## Validation

Owned loopback tests exercise compound refusal/repetition, dual source choice, historical resume,
actual MI HTTP/SQL natural planning/execution/cancellation, exact original dialogue, duplicate POST
refusal and real incremental SSE completion failures. Installed-wheel PTY acceptance checks native
Request publication, queue withdrawal and default operation without CPR. Provider fixtures,
actual local software processes, the user's supplied real-model report and real robot operation
remain distinct evidence. Field credentials, viewer authorization and robot contracts are not
changed by this implementation.
