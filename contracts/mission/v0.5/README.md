# MissionPlan v0.5

MissionPlan v0.5 retains v0.4 coordination contexts, coupling modes, typed relations, shared
views, and peer-channel declarations. It adds bounded planning inputs for the
Capability x Compute x Space x Time Scheduler.

Each Role declares `resources[]`. A demand means one exclusive resource of the given kind whose
advertised `capacity` is at least `units`; v0.5 does not introduce divisible quota accounting.
Each Task declares `timing`, expressed as millisecond offsets from durable Controller Mission
acceptance time. Estimated duration is planning evidence, not execution completion authority.

Only DAG-Ready Tasks enter scheduling. The scheduler jointly selects candidate Nodes, exact
resources, and the earliest feasible half-open time interval using a deterministic bounded search.
Control revalidates and owns future reservations. At activation, normal Proposal -> Commit -> Bind
remains the sole physical resource authority. Running work is never preempted; an overrun converts
its resource to open-ended occupancy and blocks conflicting future activation until terminal release.

Historical v0.2-v0.4 inputs remain Controller compatibility inputs and normalize to the current
domain shape. They receive immediate timing defaults and one unit for their legacy
`resource_kind`. Mission Intelligence emits v0.5.
