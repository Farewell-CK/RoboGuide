# Node contract catalog v0.14 — independent original-revoke observation

Protocol remains v0.4. Config v0.12 negotiates Node Contract v0.11 and the independent
MemoryUseQuery/MemoryUseQueryReceipt envelope fields 11/15. They carry bounded metadata only.
The original v0.1 MemoryUseControl/MemoryUseReceipt semantics remain unchanged.

Observation is separate from preparation, import, use permission and physical stopping. Each
original revoke has one five-minute/sixteen-generation session; each provider query is admitted
once before GET. Original receiver clocks survive process restart without renewal. A missing
original effect cannot be constructed or sent through this interface. Confirmed provider
evidence is appended separately; the original aggregate Unknown remains immutable.

Provider-complete confirmation still needs original context revocation and independent stop
gates before scoped release. This contract proves neither device safety nor physical continuity.
See [ADR-0107](../../../docs/decisions/0107-original-revoke-observation-and-provider-progress.md).
