# ADR-0223: Explicit compound observation acquisition

- Status: Implemented; physical validation pending
- Date: 2026-10-26

## Decision

An opted-in Local How v0.17 observation acquisition supports the declared syntax
`detected(entity-ref) [AND at(same-entity-ref)]`. The `expected` parameter remains
a string in the existing canonical `observation.acquire@v1` operation. This is a
versioned deployment condition profile, not a change to MissionPlan or Core.
Read-only verify continues to accept detection only. Old acquisition conditions
retain their meaning and never implicitly acquire a position requirement.

For an explicitly requested conjunction, local completion requires both the
existing cached detection and the same-step actual own robot reference point
within the loaded arrival tolerance of the exact target. No sensor refresh,
extra physics step, predicate query, changed threshold or model action rewrite
is introduced. Missing inputs remain unavailable. An observation is not latched
across steps or agents. Other Mission goals and official success still require
the independent final verifier.

The model receives the full committed condition and component observations.
Local viewpoint selection checks its candidate terminal stance against the
requested arrival envelope as well as its existing optical/route checks.
Intermediate departure remains permitted: this is not a continuous-residence
benchmark rule. A bounded miss remains a real local failure; recovery budgets
and physical step limits are unchanged. No automatic return command or newly
invented Task is dispatched by the adapter.

Deployment Node declarations and abstract planning classes publish this syntax
consistently. MI chooses whether a compound condition fits the actual goal; the
deployment does not force one organization, one Actor count or robot assignment.
The profile names no benchmark, episode, target label or robot class. Genuine
multi-robot cooperation and independent final official authority are unchanged.
