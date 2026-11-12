# Reported robot physical observations v0.1

Payload schema: `roboguide.world.robot-physical-observation/v0.1`.

An opt-in Connector projects only original physical reports into a separate `World` State
export. This does not rename Node health, inventory, capabilities, resources, commitments or
execution records. The original Node robot-state export remains available for diagnostics.
The deployment explicitly maps its `physical_entity_id` to the accepted physical registry.

`posture` preserves the source aggregate age, shorter stale window, original receipt and
remaining publisher budget. Missing/stale/unknown reports retain no current posture. The
source does not provide an individual posture measurement timestamp, damping or remote state.
`battery` is the reported physical battery percentage, with its own qualified availability.

`mapping` distinguishes a current HTTP response from a qualified pose measurement. Pose
measurement time, localization quality and immutable map revision stay null; localization
and shared-frame verification remain false. A saved map's identical bytes do not verify
the current physical pose of either robot.

State's outer TTL never renews a source sample. Mission admission of this schema must exclude
a report whose shorter native budget has expired after same-Controller retention plus local
read time. It must not subtract clocks from different hosts. Original values remain unchanged
for audit; rejected evidence produces an explicit grounding gap. Planning evidence is frozen;
current facts must be checked again before an authorized action.

The envelope uses the existing State epoch/sequence/receive-time/TTL contract. `reported`
is source attribution, not observed physical truth. Arrival, device stopping, shared-frame
verification and motion authorization are always false in this report. See ADR-0246.
