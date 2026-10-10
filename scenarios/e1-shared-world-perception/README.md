# Perception shared-world deployment

Explicit B1 deployment v0.3 uses the original `llm_height_per.yaml` configuration,
113-episode HSSD Perception dataset and 4,000-step budget. Spot and Drone register
their own mobility and cached `observation.verify@v1` support. Loaded class,
sensor/camera readiness and actual reset sources are checked before production MI.
One to four endpoints are supported by the generic live profile; this particular
native configuration has two. It adds no plan, pose, goal, resume or model output.

The observation parameter profile is `expected=detected(entity-ref)`; reading a
negative condition completes acquisition only. An unavailable read cannot be
fabricated and does not satisfy detection. There is no active perception policy.
Both original `any_at` and `is_detected` goals remain authoritative. The unchanged
common MI Prompts choose Tasks and dependencies from the admitted goal/evidence.

Use a fresh output directory and exact frozen B1 input with this script in
`ROBOGUIDE_B1_PREPARE_ONLY=1` mode for offline preparation. See the
[live deployment guide](../../docs/extensions/habitat-independent-live-profile.md)
for commands, release gates, bounded topology and per-attempt evidence. A new
physical preflight and reset comparison are required before releasing this
population; offline validation alone is not batch readiness.
