# ADR-0235: Immutable reported origins through Task, Memory and MI

Status: Accepted for the opt-in dog2 experiment; physical navigation is not admitted.

The combined Node Connector owns `spatial.origin.capture@v1`, a read-only local
observation operation. Normal Task allocation, execution identity and Node workflows
admit it. It never invokes Pilot, ROS actions, map loading or navigation. Its bounded
single-writer store freezes the original request, Mapping response and receive-age
limitations. Repeated executions return their original bytes; changed inputs conflict.
It does not invent a pose measurement timestamp, quality, immutable map revision or
physical entity equivalence from a Node ID.

The Connector declares an experience Memory provider. Explicit `scope=global` in
the capture operation authorizes publication of this one experiment record. Local
is the other supported scope; private origins are never automatically promoted.
Existing Node session/provider admission, Artifact CAS and Memory manifest publication
remain authoritative. Capture completion is distinct from successful publication.
One Connector process serves capture, discovery and export; there is no new gateway.

MI may read bytes only for explicitly admitted origin Memory schemas, Global scope,
exchangeable visibility, exact media type and bounded immutable Artifact identities.
It verifies the original content digest and size before interpreting origin content.
Content and its manifest provenance enter a new grounding-context/v0.4 snapshot;
old snapshot readers reject that generation. Defaults retain metadata-only behavior.
Unrecognized payloads, unavailable bytes and mismatched provenance remain gaps.
Origin content is historical reported evidence, never current localization or motion
permission. Existing approval rules must hold delegated physical plans for review.

This slice deliberately uses the existing generic experience Memory contract rather
than claiming a typed map revision or private request-Memory implementation. The
operator explicitly selects the shared experiment origin. Node/Control allocation
and local Robonix execution/safety authorities are unchanged. MI read/plan evidence
must distinguish real model calls from test doubles and never submit movement during
the initial acceptance.
