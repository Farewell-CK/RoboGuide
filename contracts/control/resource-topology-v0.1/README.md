# Declared resource topology v0.1

`POST /v1/resource-topology` uses the existing registry command envelope:
`command_id`, `expected_revision`, and complete `registry`. Both GET and POST require the global
recovery-admin authority. The registry references arbitrary deployment resource/Node identifiers;
accepting it never registers a Node or advertises capacity. It has no built-in devices.

Space declarations use inclusive integer-millimetre axis-aligned bounds plus clearance, an exact
conflict domain/frame/anchor and optional local Controller openings. Distinct IDs with intersecting
inflated bounds conflict. Unknown same-domain transforms cannot establish disjointness. Time
requires an explicit shared `time_domain`, canonical positive half-open openings and bounded
original Task duration. Distinct IDs in the same time domain cannot overlap. Touching time
endpoints may follow each other; touching spatial extents conflict. `clock=controller_receive_time`
does not translate a remote clock. `stop_timeout_ms` is the declared original evidence wait bound.
`booking_duration_ms` supplies the explicit deployment Task envelope through the existing attributed
duration port, with `booking_max_age_ms` anchored to original Mission acceptance. These are declared
admission/overrun bounds. A model supplies neither value, and they are not measured predictions.

Current declarations constrain matching/selection, complete Commit batches, Bind/Rebind and each
outbox delivery. Original declarations and booked horizons survive restart. Policy cannot change a
held/future entry. Context ownership retains the original opening horizon; sequential borrowers
cannot extend it or free the parent through Task release. Planned completion never releases actual
or Unknown ownership. Expiry delegates to existing whole-Group stop and Memory revoke; actual
independent stop proof remains mandatory. Physical exclusion and hard realtime require separately
supported Local EAIOS evidence.
