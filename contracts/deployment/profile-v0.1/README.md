# Deployment profile v0.1

The [closed schema](schema.json) freezes explicit software topology inputs and their identities.
It contains no Node inventory, physical facts, placement decisions or default devices.

Each component declares a host/control domain, exact tenant IDs, process group, original binary
and inputs. Components labelled as one process must agree on host/domain/binary. Each persistent
store declares one owner plus pinned writer/recovery contracts. Inspection rejects hardlink inode
aliases and ownership overlap. A contract version is an explicit support declaration tied to
original file bytes; it is not proof that a network protocol was exercised.

Links bind declared source/target components, protocol/contract, exact origin, actual public TLS
inputs, private client-key path and original peer leaf. Credentials are not embedded. Numeric
loopback HTTP is restricted to one host/control domain; remote plaintext, redirects/query
interpolation and cross-host Unix sockets refuse. The optional readonly probe is `none`, TLS,
health, Memory instance or inventory. It supplies no POST/Execute/model or arbitrary route.

Fault rows retain affected components, owner, trigger, expected conservative fence, exact RPO
amount/unit, RTO target and criterion references. Criterion assurance stays `software` or
`hardware`; unavailable evidence is null. File hashes only establish the declared evidence's
integrity. No JSON `passed` value silently closes a stage, renews a lease or proves an achieved SLO.

`single-controller` explicitly contains one Controller. `replicated-controller` declares 3–9
replicas on distinct host IDs and at least three fault domains; those are declarations requiring
independent deployment evidence. A same-host process check cannot establish host independence.
Zero Node is valid in either mode. Actual HA/upgrade and physical MVP gates remain separate.

The shipped inspector publishes `roboguide.deployment-inspection/v0.1` with the exact profile,
input pins, binary schema observations, declared store inode owners, local probe results and
pending criteria. A private nonoverwriting receipt freezes those bytes, without starting services
or approving a production deployment. See [the tool](../../../tools/deployment/README.md).
