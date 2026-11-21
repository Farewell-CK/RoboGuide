# Mission provenance v0.1

`GET /v1/mission-requests/{request_id}/provenance?after_sequence=0&limit=32` returns original
immutable transition snapshots. Authenticated Request tenant ownership applies before reading SQL.
Limit is 1–64. `next_sequence` advances the global immutable SQL cursor and `has_more` identifies
another page. Read/restart never invokes a provider or renews evidence time.

Configured model identity is separate from observed Provider-returned model/response ID. Missing
returned metadata stays null. These fields report the transport response; they do not attest the
backend's actual loaded model. Call IDs distinguish separate actual effects even when bytes repeat.
Credentials and full HTTP headers are excluded.

Calls through the MI urllib transport can include `http_timing`. It records monotonic elapsed
milliseconds, the offset at response headers, the time to finish reading the body after headers,
and the observed status and `x-request-id`. Waiting for headers covers connect, proxy, send and
upstream processing time. `failure_phase` identifies an incomplete request, an HTTP rejection,
or a response decoding error. A boundary the transport did not reach has a null timing.
The HTTP request ID and the JSON `response_id` identify different parts of the call.
Historical snapshots and other transports can omit HTTP timing.

Snapshot configuration contains actual installed MI source/Python/dependency identity plus the
startup-frozen Catalog/approval/configuration/Prompt hashes. An unavailable deployment lockfile is
null; installed dependency versions remain independently observed. The original deliberation binding
is indexed and stored in the same SQL transaction as its Request/provenance. Configuration changes
cannot silently reuse an existing draft for approval, resubmission or deployment reconsideration.

`recorded` means provenance was retained from Request creation in this storage profile;
`partial_legacy` explicitly lacks original historical evidence; `legacy_unavailable` has none.
Passive restart/cancel/reconciliation metadata cannot invent an original deliberation binding.
