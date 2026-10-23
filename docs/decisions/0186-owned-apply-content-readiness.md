# ADR-0186: Bound owned Controller apply on actual content readiness

Status: Accepted; implemented. Actual cold data, second restore and three-leader software acceptance passed; full HA/non-release matrices remain open.

The actual cold-restart case advanced beyond the live-producer failure after ADR-0185, then exposed
the next race: a follower applied a committed historical entry before its concurrent original byte
stream finalized. The Root correctly refused unavailable bytes, but OpenRaft stopped that follower's
apply channel even though the actual authorized stream subsequently published those bytes.

For explicit parent8 owned Controller composition, the original managed apply worker first checks
all exact qualified inputs in its retained entry batch. If an original object receipt is absent,
it waits within the same already-issued kernel budget for actual publication. It rechecks that
budget without renewal, and verifies complete actual bytes before proceeding. SQL/schema corruption
and a known receipt whose bytes are missing or corrupt remain immediate failures. No content is
fabricated, copied, imported, authorized or declared available by the wait itself.

The wait runs on the existing owned blocking worker before any new producer permit, parent mutex or
authority transaction. Network/stream workers and actual writer leases remain independent. Cancelling
a waiter retains the actual worker's original capacity and lifetime. Empty-input batches add no
wait. Direct old storage APIs keep their original behavior. Original clocks, commits, Unknown and
physical stop contracts are unchanged; no persistent or wire generation is needed.

This closes a readiness race when a real authorized publisher exists. It does not implement missing
historical-byte routing, repair a lost source, bypass capacity or establish full online HA, SLO,
hardware identity or physical continuity. Those remain independently required. Acceptance needs
actual delayed file/SQL publication, bounded expiry and the complete restored three-leader read/
Unknown case, with zero embodied Nodes/devices/models/simulators.
