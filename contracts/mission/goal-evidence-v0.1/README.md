# Original Mission goal evidence v0.1

`evidence.schema.json` describes independently published original-goal measurements. It accepts
no caller identity, role, authority leaf or receive timestamp; the actual transport supplies identity,
and Controller captures the original complete-request receive/kernel clocks before queuing or hashing.

The current Control-frozen source/predicate/context, tenant, plan revision/Group and complete real
attempt set are mandatory semantic gates beyond schema validation. Source sequences are monotonic;
replay returns the original immutable receipt. Required CAS bytes must actually verify. Unknown,
expired, revoked, incomplete or old-version evidence never completes the current Mission.

The receipt retains the actual publishing principal/leaf and original input digest/receive time.
The application separately retains nonrenewable budgets and original completion clocks. These
facts never establish device stopping, hardware identity or physical continuity.

See [the application contract](../../../docs/development/runtime-plan-revision-v0.1.md).
