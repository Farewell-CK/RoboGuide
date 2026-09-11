# Node Contract Version Guide

The numbered directories are immutable repository contract releases. A release can advance one
contract concern while retaining another, so the directory number is not a wire identity.

| Repository release | Main change | Protocol | Semantic Node Contract | Node config |
| --- | --- | --- | --- | --- |
| `v0.2` | formal bidirectional session | v0.2 | v0.2 | v0.2 |
| `v0.3` | independent artifact data plane | v0.2 | v0.2 | v0.3 |
| `v0.4` | exact readiness declarations | v0.2 | v0.2 | v0.4 |
| `v0.5` | selective State/Memory declarations | v0.3 | v0.3 | v0.5 |
| `v0.6` | Memory workflows and peer observations | v0.3 | v0.3 | v0.6 |
| `v0.7` | durable command admission | v0.4 | v0.4 | v0.6 |
| `v0.8` | capability profiles and semantic intent | v0.4 | v0.5 | v0.7 |

Canonical operation versions are independent of every identity in this table. Current production
wire/config sources are in [`v0.8/`](v0.8/README.md); older directories remain compatibility and
historical inputs and are not modified in place.
