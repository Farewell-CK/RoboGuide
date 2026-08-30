# Localization Verification Evidence v0.1

This contract is the first strong evidence envelope for `spatial.localization.verify@v0`.
It is independent from map bytes and Node Protocol transport. The Local EAIOS adapter maps its
vendor-specific observation into this canonical shape; Node Service binds the observation to the
committed Mission/Task/Group/Role and stable execution identities before persistence and delivery.

The pose-quality value and threshold are decimal strings so evidence equality and exact retry do
not depend on binary floating-point serialization. The constructor additionally requires the
reported value to pass its declared comparison. `source_observed_at_ms` remains source-local and
must not be compared with another node's clock.

The legacy `MapLocalizationVerified` event created from `has_map=true` does not satisfy this
contract. No real Robonix field mapping is claimed until a deployment validates active map, mode,
quality, frames, and source time against the actual device API.
