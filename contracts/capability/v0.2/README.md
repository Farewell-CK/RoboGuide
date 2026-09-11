# Canonical Capability Catalog v0.2

This deployment-independent catalog separates provider capabilities from canonical
operations. Mission Intelligence validates vocabulary and parameter/attribute schemas
against this file; it never consults live Node availability.

- `capabilities` define what providers may expose and which typed feasibility
  attributes Mission requirements may constrain.
- `operations` define semantic Local EAIOS goals and their minimum capability
  requirements.
- A known operation with zero live providers remains a valid Mission. Control
  Matching owns current deployability.
