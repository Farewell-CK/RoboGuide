# Memory preparation v0.2

This version preserves complete original source, typed-map provenance, accepted semantic
requirements, invocation, provider and physical-purpose metadata. It additionally requires each
receiver's explicit `use_contract`. Node Contract v0.10/config v0.11 negotiates this context;
the original preparation/import/first-Execute window remains at most 120 seconds.

The outer Prepare/Probe/Revoke command layout and availability receipt layout remain unchanged.
Their schemas in this directory resolve to v0.2 context metadata. The separate
[current-use contract](../use-v0.1/README.md) owns renewable runtime permission and never renews
the original import admission window. Imported bytes, a Probe and a use lease are separate gates.

Historical [v0.1](../preparation-v0.1/README.md) is unchanged. It cannot carry `use_contract`,
and absence never becomes a runtime capability during restore or current registration.
