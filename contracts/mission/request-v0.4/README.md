# Mission Request v0.4

Mission Request v0.4 adds one persisted `grounding_context` snapshot and binds every new semantic
Review attempt to its `grounding_context_digest`. Interpreter, Planner, Reviewer, and Repairer use
the same immutable context during one deliberation cycle. A clarification answer starts a new cycle
and captures a new dialogue-bound snapshot.

The nullable fields support restoring older v0.1-v0.3 records without fabricating historical
grounding evidence. Newly processed v0.4 requests always persist a non-null context, including a
valid empty snapshot when no evidence is available. Older request schemas remain unchanged and are
compatibility inputs only.
