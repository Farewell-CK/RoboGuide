# Deployment Planning Profile v0.2

This deployment-owned, startup-frozen artifact exposes abstract capability classes and optional
canonical-operation effects to Mission Intelligence. It contains no Node inventory, concrete
resource identity, physical entity binding, route guarantee, or current availability.

`operation_effects` lists reads and possible changes to neutral state domains. A missing operation
declaration is unknown. `partial` coverage means the listed accesses are known but other relevant
accesses may be unmodeled; `complete` means the deployment declares the full set of state domains
relevant to plan effect composition for that operation. An `actor` scope is keyed to the logical
Actor's own state and has no subject parameter. A `world` scope must name an exact semantic subject
parameter. Each `subject_parameter` and `result_parameter` names a parameter in the canonical
operation contract. Two read-only accesses do
not create an interaction finding; a read/change or change/change pair on the same domain and subject
does. These records are advisory evidence for planning and review; they do not prove a terminal
predicate, guarantee persistence, or authorize an assignment.

Every referenced domain is described once in `state_domains`; effect declarations cannot introduce
an undocumented domain name. Domain descriptions must explain the abstract state only and must not
smuggle in a concrete Node, Resource, or Physical Entity selector.

The parser continues to accept v0.1 artifacts with their original meaning. They do not gain
operation-effect facts during migration.
