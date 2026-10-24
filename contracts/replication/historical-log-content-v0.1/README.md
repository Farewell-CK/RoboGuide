# Historical retained-Log content v0.1

This explicit private mTLS protocol adds `HistoricalLogTargetV1`, `RouteHistoricalLogV1` and
`PushHistoricalLogContentV1` to the maintained Replica framing. It is not a public Artifact read,
application Commit, proposal staging or quorum-success API. Old peers refuse unknown commands.
See [ADR-0187](../../../docs/decisions/0187-retained-log-content-catchup.md).

The source describes an exact locally retained `LogId` only after checking its original producer
and durable committed position. The receiver independently needs the same complete durable Log
and original active producer before Ready. The original historical candidate fixes batch/checkpoint
digest and all qualified `(source_owner,digest,size)` inputs. The target's revision freezes the
source's observed accepted application revision; it is not the historical candidate's application
revision or a promise that the receiver has applied it. Only these new commands use that context.

Both actual members require the same currently applied consensus membership, current epoch, exact
Root ownership and actual current TLS credentials. A requester cannot choose a different destination:
the authenticated requester is the receiver. Original stream IDs derive from profile/source/receiver/
Log/input, excluding epoch or retry count. A changed epoch cannot replace an original Unknown ID.

Actual receive intent and independent pin precede Ready and source payload opening. All actual
SQL/hash tasks retain bounded Root work capacity through their real lifetime, including after async
cancellation. Each chunk rechecks the original Log and live authority without reacquiring a Root
mutex held by its own stream. The source must retain Commit; the receiver must retain its exact Log.
The same original ingress clock covers discovery and transfer requests. No request renews a retained
stream or opens a second source after an original Unknown.

Successful authenticated Append dispatch uses only a bounded already-durable unapplied Log prefix
and its actual authenticated sender. Consensus-only membership prefixes are applied before a later
content command waits for bytes, under the same original worker budget. Each application command
remains atomic and unavailable content prevents its authority. Missing receipts, corrupt known bytes
and failed publication remain separate outcomes.

The first slice does not cover learner/joint membership differences, network Snapshot content,
missing historical source repair, complete GC/upgrade matrices, cross-host or physical continuity.
It creates zero embodied Nodes/devices/models/simulators. Native conformance extends
`tools/quality/check_network_content_processes.py --historical-log-catchup` using a real stopped
member, existing original Controller SQL/CAS, actual restart, independent SQL/files and original waits.
