# Three-agent Rearrangement candidate

Explicit B1 deployment v0.3 preserves the available original
`llm_multi_agent_mobility.yaml`, `mp3d_episodes_1.json.gz` (100 episodes),
Spot/Fetch/Drone configuration and 5,000-step budget. Its actual goal relocates
two objects; a navigation-only plan cannot establish those object goals. Only
Spot and Fetch declare `object.relocate@v1`; Drone retains navigation support.
The shared world activates only actually dispatched independent Tasks, keeps
unassigned endpoints on original model-free wait, and reuses one reset world.

This is a candidate, not a certified Task4 paper population. The local original
README references `llm_spot_drone_rearrange.yaml`, which is missing. Resolve
that native mapping before claiming formal Task4 coverage or dispatching a batch.
Do not substitute an orphan dataset merely to obtain a runnable filename.

The original configuration randomizes reset and writes its initialization JSON.
Paired workers must declare
`data/robots/robot_configs/mp3d/mp3d_episodes_1.json` as a private
`vendor_writable_assets` target. Both arms preserve the same initial bytes (or
the same missing-file state) and original random process. Actual poses must be
compared; the seed does not certify equal starts. Do not edit shared EMOS assets.

Prepare offline with an exact frozen B1 input and a fresh output directory.
See the [live deployment guide](../../docs/extensions/habitat-independent-live-profile.md)
for commands, identity gates, explicit Local How differences and physical release
requirements. Common MI Prompts and benchmark/Formal rules are unchanged.
