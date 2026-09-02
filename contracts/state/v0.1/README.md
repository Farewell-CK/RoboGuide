# Source-aware State Contract v0.1

State records use schema `roboguide.state-record/v0.1`. A record identifies one `Node`, `World`,
or `RoboGuide` object and one of `Desired`, `Committed`, `Reported`, `Observed`, `Derived`, or
`Belief` semantics. Source and channel remain part of the key, so independent observations never
collapse into an implicit global truth.

Node Protocol v0.3 accepts only State exports declared by the current complete registration
snapshot. Node exports are limited to `Reported` and `Observed`; authoritative `Desired` and
`Committed` views remain with Mission Orchestration and Control. Payloads are bounded JSON with a
versioned payload schema, receive-relative TTL, optional source-local time, and optional confidence.
RoboGuide receive time determines ordering and freshness. An optional Node session epoch only
disambiguates records received in the same millisecond after reconnect; it is not part of the
semantic source key and does not make source-local clocks comparable.

The Controller read facade exposes provider discovery at `GET /v1/state/providers` and filtered
records at `GET /v1/state/records`. It federates existing owners and has no generic write or
authority-changing operation.
