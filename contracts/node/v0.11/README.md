# Node contract catalog v0.11

Wire transport remains `roboguide.node-protocol/v0.4`. The generated protobuf messages remain
compatible; this catalog explicitly negotiates semantic `roboguide.node.v0.8` for config v0.9.
Old Controllers cannot admit the new profile by silently ignoring protobuf fields. Profiles v0.4–v0.7
remain explicit compatibility inputs, including original-provider readonly Memory continuity.

Config v0.9 requires every typed-map output binding to identify its original registered Spatial
provider and exact content schema. Provider kind/scope/visibility/media/schema must match its
declaration. Runtime use also checks the executing operation's LocalSystem owner before Local How.
Node produces [typed-map v0.2](../../spatial/v0.2/README.md), preserving actual build Task/attempt,
original LocalSystem and provider. Missing historical identities are not filled in automatically.

Publication carries the current accepted Node session and a distinct Runtime publishing attempt;
these values are context rather than credentials. The actual Artifact client certificate and
current Controller route authorize them. Original build provenance stays immutable and cannot be
replaced by the later publishing Task. Controller checks existing Control/Runtime authorities under
the catalog writer gate before committing the event.

Runtime commands freeze the actual dispatch-time Node Contract, accepted source session and registered
Memory provider declarations. These internal evidence fields stay outside semantic model parameters
and Node selection. Retry and checkpoint restore retain the original snapshot, including unavailable
legacy fields. Later registration cannot fill them in. Before Execute delivery, changed source
session/contract or missing original declarations fences the pending command for explicit reconciliation;
no physical ownership is released. Publication checks the original build and publishing snapshots
against the exact current provider, so a software upgrade cannot relabel old work as a new provider.

Node journal authority v8 fences the actual previous v7 binary using `user_version` and the durable
schema marker. Existing v7 table names/layout remain unchanged, as do readonly legacy SQL views;
the v7 marker migrates atomically to v8. Old v6 SQL writers remain fenced by those readonly views.
Controller wrapper v33 and map evidence codec v24 independently fence earlier metadata writers.

Distributed Group grants/revocation, pre-Execute Memory preparation, active-map and GC remain
required F4 work. This contract does not grant hardware identity, stop or execution continuity.
