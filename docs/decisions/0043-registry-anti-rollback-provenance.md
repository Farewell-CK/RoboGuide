# ADR-0043: Durable registry anti-rollback provenance

- Status: Proposed for review
- Date: 2026-09-17

## Decision

Control persists a registry identity, highest successfully admitted revision, and SHA-256
content digest independently of ActorBinding provenance. The digest uses a versioned prefix,
the typed routing profile, and length-framed entity/Node identifiers in canonical entity order.
It stores no recoverable routing table. Equal revision requires equal content; lower revisions
and changed registry identities fail closed, including after restart and before first binding.
The watermark advances only after all existing binding checks pass.

ActorBinding retains its original bind-time revision. Checkpoint restore cross-checks that
binding provenance belongs to the admitted registry and does not exceed its watermark.
Current registry remains absent after restore: deployment must supply it, and existing routing
and missing-registry fences still apply. This adds no topology, reservation, or migration
authority and does not let ordinary Recovery move an Actor.

## Compatibility

The inner controller checkpoint advances from v14 to v15; the server wrapper advances from
v15 to v16. Each accepts its immediately preceding marker. Historical logical checkpoints
without physical bindings may restore with no watermark and establish it at first registry
admission. A historical checkpoint with physical bindings but no watermark is rejected with
an explicit trusted-migration diagnostic: the latest admitted revision/content cannot be
reconstructed from older bind-time evidence. An operator migration needs independently trusted
deployment history; this change does not invent it or provide a silent downgrade path.

## Validation

Regressions cover bind at revision 1, admission at 5, restart and rejection of 2; equal-revision
changes to unbound routes; canonical registration order; failed updates not advancing the
watermark; zero-binding checkpoints; missing current registry; and legacy/malformed provenance.
