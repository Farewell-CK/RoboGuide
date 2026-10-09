# Deployment Recovery Session v0.1

This is immutable MI evidence for optional reasoning before the first Controller
submission, as defined by [ADR-0067](../../../docs/decisions/0067-bounded-mi-deployment-reconsideration.md).
It is not a MissionPlan extension, executor selection, physical recovery command
or resource authority.

`session.schema.json` declares the bounded envelope. Production domain checks
also require canonical input-plan SHA256, exact assessment HTTP-body SHA256,
logical slot attribution, source lifetime, ordered nonrenewable count/time bounds
and one frozen context/source identity across all attempts. B1 additionally binds
each attempted input to a real approved review in the request's frozen context.
JSON Schema alone does not establish those relationships or semantic equivalence.

The session contains `request_id`, `mission_id`, `max_attempts` (1..3), original
`started_at_ms`/`expires_at_ms` (at most 900000 ms apart), and ordered `attempts`.
Each attempt freezes:

- Complete `input_plan`, canonical `input_plan_digest`, `grounding_context_digest`
  and exact [assessment v0.2](../initial-operation-assessment-v0.2/README.md).
- MI-local start/finish times; `pending`, `decided`, `failed`, `interrupted` or
  `expired` outcome; error type when a call failed.
- Complete bounded raw `provider_output` where available and canonical `decision`.
  Only `revise_plan` may contain a replacement plan. A proposal remains unapproved
  until ordinary deterministic validation and new Review/risk approval complete.

Each stored input/raw document is limited to 262144 UTF-8 bytes. Oversized output
is explicitly failed and not silently stored as a complete response. Decisions
have a nonblank explanation of at most 4096 characters. Pending/interrupted calls
do not fabricate responses. An expired decision remains evidence but is not
applied. Discarded proposals are not represented as submitted/reviewed plans.

Observations v0.4 require this session in `deployment_recovery`; without one,
production keeps observations v0.3. The public Mission Request remains v0.4.
Restore and provenance reject a legacy envelope that smuggles a session, a v0.4
envelope with missing/invalid session, detached digests or renewed budgets.

The production port uses the configured Repairer Prompt/model/reasoning settings,
strict Responses decision schema and same frozen task inputs. It never exposes
Node/Resource inventory or calls Planner/Interpreter as part of reconsideration.
Opt-in is `service.max_deployment_recovery_attempts=1..3` with Controller preflight
and Reviewer enabled; `service.deployment_recovery_timeout_ms` is 1..900000 and
defaults to 900000. Zero attempts retains ADR-0066's unchanged-plan recheck.
