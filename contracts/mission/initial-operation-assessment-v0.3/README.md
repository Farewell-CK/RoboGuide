# Initial operation assessment v0.3

Extends v0.2's bounded, plan-bound diagnostic projection with `identity_unavailable`.
The Controller emits v0.3 when this reason occurs; legacy v0.1/v0.2 remain readable and cannot
carry the new reason. The Python consumer preserves exact slot coverage and counter partitions,
and rejects Node inventory, mismatched plans or invented exclusions.

This diagnostic distinguishes certificate/attestation eligibility from capability support.
It does not grant a reservation, physical identity, Task satisfaction or model-selected Node.
