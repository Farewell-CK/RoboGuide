# ADR-0256: Native Chat task tools and recoverable conversation

Status: accepted for implementation, 2026-11-16.

The user wants Chat to pursue an explicit natural-language goal through discovery, clarification,
MI and progress reads. The former default only proposed `/choose` commands and therefore required
another mechanical confirmation even for an already explicit task request. Real-use evidence at
`c17d07f9` also found a compound query losing its independent Mission read after a reference refusal
and repeated directory call. Passing synthetic tests did not establish real-model usability.

## Decision

The default native model receives closed task tools: `start_task`, `answer_request`, `read_request`,
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

## Validation

Owned loopback tests exercise compound refusal/repetition, dual source choice, historical resume,
actual MI HTTP/SQL natural planning/execution/cancellation, exact original dialogue, duplicate POST
refusal and real incremental SSE completion failures. Installed-wheel PTY acceptance checks native
Request publication, queue withdrawal and default operation without CPR. Provider fixtures,
actual local software processes, the user's supplied real-model report and real robot operation
remain distinct evidence. Field credentials, viewer authorization and robot contracts are not
changed by this implementation.
