# Explicit deployment inspection

`deployment_profile.py` inspects an operator-authored
[deployment profile v0.1](../../contracts/deployment/profile-v0.1/README.md). It starts no services,
submits no Mission and registers no Node. There is no built-in robot, capability or fake inventory.

```sh
python tools/deployment/deployment_profile.py --profile /absolute/profile.json \
  --output /absolute/new-private-receipt.json --probe
```

Omit `--probe` for local-file/configuration inspection only. A profile lists actual component
process groups, hosts, control domains, exact tenants, pinned binaries/inputs, persistent store
owners, contract versions, trust links, fault targets and explicit exit criteria. Missing binary
pins and evidence remain visible. The closed schema does not supply unknown facts or default
device addresses. Raw loopback HTTP is restricted to the declared same host and control domain;
other TCP boundaries require real TLS material and an exact expected server leaf. Unix transport
is same-host only. Actual TLS chain/name/leaf checks precede fixed readonly HTTP probes. gRPC
inspection performs TLS/ALPN only and never registers a session or proves protocol compatibility.

Schema inspection invokes only a pinned Controller/Replica binary with `--application-schema`.
File and store identities are checked again before publishing. Input/response counts, bytes,
subprocess/network timeouts and the original inspection budget are bounded. The receipt records
an actual local hostname/Linux boot and which links were probed from that inspector; declared
remote hosts, fault-domain independence and process placement do not become observed facts.

The result is a new private immutable input/observation receipt. It does not create execution,
content or deployment authority. It preserves the complete declaration under its exact digest;
an evidence file's integrity is distinct from semantic acceptance, and RTO/RPO remain targets.
It never automatically announces project SoftwareComplete or hardware safety. Reinspect current
inputs/endpoints before using a historical receipt for operational review. See
[ADR-0129](../../docs/decisions/0129-executable-deployment-profile.md) and the
[actual zero-Node acceptance](../../docs/development/acceptance/2026-10-21-deployment-profile.md).
