# Reported Scene targets v0.2

Payload schema: `roboguide.world.scene-targets/v0.2`. Publication is opt-in on the existing
physical-observer World channel; it does not rename Node or Control facts.

`definition_json` is the only target-definition authority: a bounded strict canonical JSON
string with `physical_entity_id`, `source_node_id`, `map_binding`, and sorted original `regions`.
`region_source_digest` is SHA256 of its exact UTF-8 bytes. Consumers verify those bytes before
parsing finite geometry, source attribution and identities. They do not reconstruct the digest
from floating numbers that have passed through a different JSON parser. Duplicate keys, a changed
string, foreign identity, malformed polygon or conflicting digest are rejected.

Current reports include original sample receipts, monotonic sample age, remaining publisher
budget and source HTTP body digest declarations. The sampling window is 15s, with one 5s total
budget for fixed state/regions/state reads. Outer State TTL never refreshes original sample age.
Consumers deduct same-Controller retention plus local elapsed time before use and again before
freezing later acquired evidence. Source timestamps are not compared across hosts.

Unknown mode or generation remains null/empty as originally reported; map identity and source
must exist and the two original state bindings must agree. This does not prove atomicity or an
immutable revision. `localization_verified` and `arrival_verified` remain false. Missing,
unavailable or expired source data never becomes motion authority.

v0.1 remains historical failed field evidence: typed floating JSON changed across the transport,
so its canonical target digest could not be reproduced. v0.2 refuses to silently reinterpret it.
See ADR-0247 and the dated field acceptance records for deployment and physical-effect limits.
