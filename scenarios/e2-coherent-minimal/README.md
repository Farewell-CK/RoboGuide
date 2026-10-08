# E2-S0C COHERENT controlled physical closed loop

The scenario submits one fixed MissionPlan through the production Controller and
Node Protocol. The selected Node invokes the loopback COHERENT Local EAIOS, which
runs the already-validated `Merom_1_int_Task1` physical plan in `coherent-sim`.

The verdict requires all of the following: one Controller-authorized dispatch,
RoboGuide Mission completion, COHERENT plan completion, all 9 text actions and 21
physical skills, all three robot identities, and the independent final-state goal
checker. No result is edited by hand.

This controlled smoke intentionally treats the pre-authored Trio plan as one canonical
operation. It is not autonomous physical planning and it does not expose or schedule the
Dog, Drone, and Arm operations separately. `autonomous-readiness.json` records the missing
operation/observation/cancellation contracts. `verify-autonomous-readiness.py --expect blocked`
is a fail-closed claim gate: until every required interface has machine-verifiable evidence,
this directory cannot be reported as E2-S0A.
