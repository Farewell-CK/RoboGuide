# Federation execution v0.1

See [ADR-0211](../../../docs/decisions/0211-durable-cross-domain-coordination.md) and
[the authority contract](../../../docs/development/federation-coordination-v0.1.md).
Execution requires explicit federation authorization v0.2 delegation and actual mTLS.
`POST /v1/federation/transactions` admits a complete immutable Manifest at the root.
`GET /v1/federation/transactions/<original-id>` reports durable root evidence.
`POST /v1/federation/executions/{probe,prepare,commit,release,abort,stop}` repeats the
complete Manifest and original nonce; original actual epoch is mandatory after probe.
`GET /v1/federation/executions/<original-id>` observes without repeating effects.

Original participant v0.2 remains metadata-only. No device, physical continuity,
cross-host validation or complete software acceptance follows from this schema.

New v0.1 identities use the `roboguide.federation-identity/v0.1` hash domain, a zero byte,
and canonical JSON over explicit null/boolean/string/integer/float64/array/object tags.
Integers retain decimal spelling, floats retain exact IEEE-754 bits, and literal parameter objects
cannot alias float tags. Shared Rust/Python identity fixtures include small exponents and negative
zero. Legacy participant v0.2 identity is unchanged.
