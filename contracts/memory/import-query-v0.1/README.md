# Original-provider import query v0.1

`POST /v1/memory/import-queries` is administrator-authorized. Its request names the retained import,
expected generation, absolute Controller deadline and per-query timeout. The first request freezes
a session of at most five minutes / sixteen queries; later requests must retain that deadline.
An exact HTTP retry returns the original query. Global retained history is bounded at 1024 queries.
Each query has at most four deliveries and thirty seconds. Deadline, receive times, generations
and counts survive restart. Payload bytes never enter this protocol or Controller checkpoints.

The Node queries only the backend instance and configuration contract retained before the original
import. Each query is durably admitted before its readonly Local EAIOS operation, and the same
query identity cannot read again after interruption. Results must bind every original identity in
`provider-result.schema.json`; `completed` and `rejected_before_effect` additionally require
`durable=true`. Unknown, absence, evicted content and missing legacy identity retain ambiguity.
Backend query cannot reimport, choose a provider, restage content or create another import ID.
Unresolved original provider/selector commands fence replacement IDs.

A valid current-authority result can reconcile the original Memory operation. A late result or a
result after changed Control/Runtime ownership is history only. Neither result changes Task
satisfaction, completes execution, releases reservations or grants current availability. The full
first receipt, original principal, backend identity and admission times are retained and audited.

`integrations/memory-provider` is an actual immutable-file backend with bounded explicit staging
roots. Its readonly query independently checks the durable original manifest and SHA-256 bytes.
It is a software Local EAIOS profile and claims no robot, physical pose or hardware identity.
