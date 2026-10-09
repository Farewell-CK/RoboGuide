# E2 Node-loss recovery pilot

This directory adds an experiment-only fault boundary around the unchanged RoboGuide runtime.
It does not modify Mission Intelligence, Controller, Scheduler, Node, or the COHERENT adapter.

The frozen pilot runs `env4/task17` six times with `gpt-6.1-sol`, the `fair` prompt and a serial
DAG: three F0 clean controls and three F1 Node-loss replicates. F1 blocks primitive request 7
before it reaches COHERENT, terminates the bound primary Node after six completed primitives, and
registers a distinct standby Node advertising the same operation. Recovery must therefore come
from the existing Controller path; the harness never selects, edits, or executes a replacement
action.

This is deliberately not an F3/F4 experiment. Ambiguous post-effect execution and
completed-but-unsatisfied reconciliation need separate, frozen semantics and verifier evidence.
Mixing them into this six-run pilot would make the comparison uninterpretable.

Each run preserves the normal DAG evidence plus `fault-timeline.json`, graph snapshots,
`fault-verdict.json`, primary/standby logs, Controller attempts, and a refreshed `SHA256SUMS`.
