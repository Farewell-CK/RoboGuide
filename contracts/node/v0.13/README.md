# Node contract catalog v0.13 — current-use lease implementation

Wire transport remains v0.4. Config v0.11 explicitly negotiates semantic Node Contract v0.10;
`use_contract` declares continuous current-content permission separately from original preparation.
MemoryUseControl/MemoryUseReceipt occupy new envelope fields 10/14. They carry metadata only.

The original 120-second preparation/import/Execute admission window is unchanged. A separate use
lease binds the original complete source/purpose/configuration, current policy and membership,
contiguous generation and at most 30 seconds of original receiver time. Replay never renews time;
revocation is irreversible and may arrive first. All selected backend acknowledgements for the
latest generation precede Group pending Execute. An older live grant supports existing work
while renewal is pending; it cannot admit new Execute. Backend ambiguity retains ownership and
triggers existing stop/hold rules.

The reference `roboguide.memory-use/shared-kernel/v0.1` Local How requires Node and Local EAIOS on
one actual Linux kernel and same-user private Unix endpoints, validating real boot/uptime budget
before effects and every bounded content-use phase. Core consumes transport-neutral receipts;
it does not compare clocks from independent hosts or turn content fences into physical stop proof.
Exact Rust-encoded preparation JSON is hashed as original bytes, preserving existing float encoding.

Actual Controller/Node dispatch, runtime renewal/revocation/expiry/restart have passed software
process conformance. Active-map, scope handoff and GC acceptance remain mandatory before F4 closes.
