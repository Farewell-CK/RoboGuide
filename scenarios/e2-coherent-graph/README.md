# E2-S1 COHERENT graph controlled closure

This scenario runs public COHERENT `env4/task17` through the production RoboGuide
Controller and three formal Node Protocol clients. The fixed 13-step GT-length plan
is split into four dependency-gated tasks: Arm load, Drone lower, Dog load, and Drone
deliver.

The graph adapter calls the original COHERENT `Get_env_info.step` implementation and
uses its original goal result. It additionally fails closed on explicit action
preconditions, persists graph state and local handles in SQLite, and writes one raw
JSON evidence file per phase. This is a controlled fixed-plan experiment, not PEFA or
Mission Intelligence.

Run from the RoboGuide repository after building `integration-server` and
`roboguide-node`:

```bash
bash scenarios/e2-coherent-graph/run-controlled.sh
```

Success requires four Controller-selected task assignments to the intended Arm,
Drone, and Dog nodes, four completed Node workflow executions in dependency order,
13 original graph transitions, and all three task17 goal relations.
