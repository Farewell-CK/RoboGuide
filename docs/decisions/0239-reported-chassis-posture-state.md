# ADR-0239: Reported chassis posture through ordinary RG State

Status: Accepted for the opt-in dog2 Connector profile.

RG must inspect reported robot state without creating a Pilot task for each query.
The existing combined Connector optionally samples only the fixed Robonix
robonix/primitive/chassis/state contract. Atlas provides the active source and MCP
endpoint, as for the Robonix client. The observer calls only the state tool, closes
its MCP session and Atlas channel, and publishes a cached observation in the existing
robot-state Node export. No action contract, driver/lifecycle command, Pilot request,
extra service or action-registration mechanism is added.

The opt-in --chassis-state profile runs in the existing observation thread. Host
observations may include the same unchanged chassis evidence. Readers use only RG
Controller inventory and State GETs, requiring exact Node/object/source/channel,
nonexpired State, session/sequence metadata and bounded nested aggregate age. They
must refuse stale, unknown, unavailable, duplicate, foreign-source or unreachable data.
Reading cached HTTP State cannot refresh the original source age. Fixed local MCP
endpoint, bounded replies/budgets and redirect refusal prevent arbitrary tool/URL use.

Robonix supplies aggregate ts/age/stale and posture. It does not expose an individual
posture timestamp, remote-controller connectivity or damping mode here. Keep those
gaps explicit. The reader reports the attributed aggregate; it grants neither movement
permission, hardware attestation, physical-effect verification nor stop evidence.
Do not derive posture independently from knee angles inside RG or fabricate missing modes.

This is an additive opt-in observation on lite3.robot-state/v0.1, not an MI World schema
or public World promotion. The deployment's normal source/freshness admission remains.
Tests cover actual local MCP messages and cleanup, cache expiry/clock rollback, exact
Controller source selection, unknown/stale/foreign-source refusal and unchanged adapter
regressions. Field checks must separately establish Controller-only reads, expiry/recovery,
no new native tasks and retained original journals before executing a new authorized goal.
