# Canonical Capability Catalog v0.3

This deployment-independent catalog separates provider capabilities from canonical
operations. Mission Intelligence validates vocabulary and parameter/attribute schemas
against this file; it never consults live Node availability.

- `capabilities` define what providers may expose and which typed feasibility
  attributes Mission requirements may constrain.
- `operations` define semantic Local EAIOS goals and their direct provider-level
  feasibility baseline. `required_capabilities` does not enumerate the internal
  grasp, navigation, perception, or other Local How steps of an integrated operation.
- A known operation with zero live providers remains a valid Mission. Control
  Matching owns current deployability.
