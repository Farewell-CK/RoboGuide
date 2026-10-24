# Runtime plan revision v0.4

[ADR-0203](../../../docs/decisions/0203-typed-map-runtime-revision.md) adds the explicit
`authorize-memory` action with mandatory complete original `typed_map`. Other actions keep their
existing v0.1/v0.2/v0.3 contracts. A v0.3 declaration cannot carry or acquire typed-map authority.

The complete map must equal the published source and an original preparation in the consuming
source Group. The child retains the exact source projection's owner, provider, bytes, payload and
production provenance; only selector, narrowing Group scope and creation time may differ. The
original map's producing Mission may differ from the consuming Mission and is never rewritten.
The complete typed policy and explicit target Read/Import grant are independent checks.

Declaration creates no Group, reservation, import or Active Map. Review covers the complete original
source. Existing original stop/revoke, kernel budgets, CAS, normal Control and per-Task preparation/use
gates remain mandatory. The scoped child is generic Spatial Memory and does not assert a new map,
frame, localization result or device capability. Wrapper v55 fences older writers.

At most 16 declarations per candidate and 512 KiB retained declaration metadata remain unchanged.
The closed JSON schema checks structure only; it never establishes current permission or actual bytes.
