# ADR-0199: Preserve typed-map provenance through Group handoff

Status: Accepted and implemented; cross-stage combination remains open.

The existing Group Memory handoff accepts a generic `MemoryArtifactManifest`. That loses the
Spatial manifest's frame, anchor, format, source execution and provider-qualified lineage at the
handoff boundary, even though typed-map import/use already requires those fields. A generic
projection must not authorize a typed-map handoff.

Typed-map handoff therefore carries the complete original `MapArtifactManifest` beside the generic
child projection. Control verifies the projection is exactly derived from the typed manifest,
checks the original typed-map policy and principal Handoff grant, then applies the existing scope,
tenant, immutable owner/provider/bytes and target-Group checks. The typed manifest is retained in
the durable handoff request and rechecked on retry, restart and commit. Existing v0.1 generic
handoffs remain valid with an absent typed field; they cannot be relabelled as typed maps.

This preserves metadata authority only. It does not select an active map, prove localization,
grant current use, stop a device, or establish physical continuity. Full provider byte import,
active-map selection and revoke/GC remain separate gates.

Required acceptance covers typed source/child projection mismatch, missing or changed typed policy,
tenant/principal rejection, generic compatibility, durable retry identity and restored handoff
history. No device or simulator is required.
