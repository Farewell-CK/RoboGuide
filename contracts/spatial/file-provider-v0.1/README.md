# Spatial file computation contracts

These contracts belong to the deployment Spatial provider, separate from Node Protocol, Control
authority, localization evidence and the typed distributed Map catalog. They contain no device IDs,
Node selection, resource ownership or automatic replication authorization.

| Contract | Purpose |
| --- | --- |
| `point-cloud-job.schema.json` | Exact PLY/PCD source hashes/units/frames and bounded SE(3) quality/work policy |
| `point-cloud-map.schema.json` | Actual measured graph, immutable observed-point tiles and parent lineage |
| `occupancy-job.schema.json` | Separate bounded planar SE(2) search, geometric quality and coverage policy |
| `occupancy-grid.schema.json` | Actual ROS-like row-major values -1/0..100 with x/y/yaw and declared source units |
| `occupancy-map.schema.json` | Immutable sparse planar occupancy tiles retaining unknown and conflicts |

JSON Schema enforces field/type/structural bounds; provider admission also checks aggregate budgets,
exact identities/path components, header dimensions, search cardinality, frame/parent consistency,
current bytes, geometric degeneracy, measured overlap/residuals and immutable tile indices. Schemas
alone never prove the content is valid or authorize a side effect. Backend/native process limits
enforce the original nonrenewable work budget. Exact-job observation does not launch another worker.

Planar registration searches configured yaw and centroid-relative translations, refines measured
correspondences using rigid ICP, optimizes a fixed-anchor pose graph, then rechecks all actual edges.
Unrefined competing hypotheses, ambiguous/disconnected or degenerate geometry remain unavailable.
Planar output is SE(2); there is no inferred altitude or fabricated full-SE(3) pose.

Occupancy classification uses configured free/occupied thresholds; -1 and intermediate values stay
unknown. Output free requires one explicit observed free input square to cover the **whole** target
cell under the estimated transform and configured geometric margin. Occupied footprint overlap
retains occupied evidence; contradictory free/occupied facts retain mask 3. Missing cells are unknown,
not free. Parent conflicts remain conflicts and cannot supply unambiguous occupied registration
correspondences. This conditional geometric coverage is not calibrated pose uncertainty or physical
navigation safety. No result creates current Node localization, covariance or Mission satisfaction.

Input bytes and accepted transforms remain inspectable; a parent hash never becomes a fresh device
observation. The [provider](../../../integrations/spatial-provider/README.md) and its actual file checks
produce software evidence with zero devices. Node/CAS publication, active-map consumers and distributed
authorization are separate F4 work and must be verified through their formal authority boundaries.
