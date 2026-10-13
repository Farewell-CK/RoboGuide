# Perception shared-world deployment

Explicit B1 deployment v0.4 uses the original `llm_height_per.yaml` configuration,
113-episode HSSD Perception dataset and 4,000-step budget. Spot and Drone register
their own mobility, cached `observation.verify@v1` and bounded
`observation.acquire@v1` support. Loaded class,
sensor/camera readiness and actual reset sources are checked before production MI.
One to four endpoints are supported by the generic live profile; this particular
native configuration has two. It adds no plan, pose, goal, resume or model output.

Both observation operations use `expected=detected(entity-ref)`. Verify reports
the existing cache without moving. Acquire uses original navigation/wait for at
most 256 local cycles and completes only on actual detection; a negative or
unavailable read cannot satisfy it. Acquisition does not establish arrival or an
arbitrary conjunction. The deployment also enables at most two model action
reselections per attempt following a pre-dispatch Contract Guard rejection.
These controlled Local How differences are frozen and disclosed.
Both original `any_at` and `is_detected` goals remain authoritative. The unchanged
common MI Prompts choose Tasks and dependencies from the admitted goal/evidence.

Use a fresh output directory and exact frozen B1 input with this script in
`ROBOGUIDE_B1_PREPARE_ONLY=1` mode for offline preparation. See the
[live deployment guide](../../docs/extensions/habitat-independent-live-profile.md)
for commands, release gates, bounded topology and per-attempt evidence. A new
physical preflight and reset comparison are required before releasing this
population; offline validation alone is not batch readiness.

MI's per-call timeout is 1,200 seconds. Freeze proxy, MI observation, Mission
observation and outer arm waits before execution; the pair worker checks their
minimum consistency before starting services. See the live guide for an explicit
budget example and its limits. This does not add retries or alter native EMOS.
