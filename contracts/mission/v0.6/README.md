# MissionPlan v0.6

MissionPlan v0.6 retains v0.5 coordination, resource, and scheduling declarations. It adds one
explicit Task satisfaction policy so local execution completion no longer silently means the
Task's semantic outcome is satisfied.

Each Role declares `resources[]`. A demand means one exclusive resource of the given kind whose
advertised `capacity` is at least `units`; v0.5 does not introduce divisible quota accounting.
Each Task declares `timing`, expressed as millisecond offsets from durable Controller Mission
acceptance time. Estimated duration is planning evidence, not execution completion authority.

Only DAG-Ready Tasks enter scheduling. The scheduler jointly selects candidate Nodes, exact
resources, and the earliest feasible half-open time interval using a deterministic bounded search.
Control revalidates and owns future reservations. At activation, normal Proposal -> Commit -> Bind
remains the sole physical resource authority. Running work is never preempted; an overrun converts
its resource to open-ended occupancy and blocks conflicting future activation until terminal release.

Every Task declares `satisfaction.basis`. The only v0.6 basis is `execution-report`: after all
current Role executions report successful terminal outcomes, Orchestration may accept that report
as Task satisfaction evidence. This bootstrap policy does not claim independent physical-world
verification. The Task `description` remains the reviewed human-readable expected outcome.

Historical v0.2-v0.5 inputs remain Controller compatibility inputs and normalize to
`execution-report`. Mission Intelligence emits v0.6. Future State- or verifier-backed bases require
a new contract version and must preserve Runtime execution facts separately from Task satisfaction.
