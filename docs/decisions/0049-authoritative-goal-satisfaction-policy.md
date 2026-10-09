# ADR-0049: Explicit authoritative-goal Task satisfaction policy

- Status: Proposed for review
- Date: 2026-09-28

## Context

In one real Episode51 seed40 run, production MI produced a single
`mobility.move@v1` Task whose expected effect claimed the full joint terminal
goal but whose satisfaction basis was `execution-report`. Controller accepted
the local `Completed` and recorded `TaskSatisfied(basis=execution-report)`;
Habitat separately reported official success. The verdict source and verdict
artifacts existed, but the Task did not consume them. The run was successful,
yet it did not test the new verifier ingress.

The Catalog describes `mobility.move@v1` as moving to a semantic destination.
It does not guarantee that a local successful report independently establishes
an environment-authoritative joint terminal predicate. In the shared-world
adapter, a local navigation skill can report `Completed` before the official
goal is true; the adapter may also report `Completed` after an official
success. Those two paths are distinguishable in local evidence, but the
generic `execution-report` basis does not encode their distinction. A
deployment that requires independent final-world confirmation therefore
needs an explicit policy, not an inference from task words or predicate count.

## Decision

Mission's startup-frozen satisfaction policy may include an exact
`authoritative_goal_verifier` contract. The B1 shared-world deployment opts
in through its own Mission config. The ordinary Mission config retains the
previous receive-age-only policy. When both this policy and a frozen
environment-authoritative joint goal are present:

1. The exact full goal must be representable by the same bounded predicate
   syntax advertised by the deployment's verifier source. Unsupported
   operators, quantifiers, or identifiers are a system capability gap before
   a Provider call; MI does not replace them with a weaker expression.
2. Planner, Reviewer, and Repairer receive the policy digest and the exact
   predicate derived from the frozen goal. Every Task with no DAG dependent
   must declare `verifier-evidence` with that contract, predicate, and the
   configured RoboGuide receive-age bound. Earlier prerequisite Tasks may
   use `execution-report` for their own local outcomes.
3. MI validates this deterministically before Review or Controller
   submission. A wrong basis, partial predicate, or wrong contract is a
   rejected draft eligible for the existing bounded pre-validation recovery.
   The verifier cannot be supplied by a fabricated meta-Task or a changed
   goal. Controller still admits only the actual reset-frozen source and
   applies evidence against the current physical attempts.

Requiring all DAG sinks allows two independent Tasks to run concurrently
while both await a final verdict, or one Actor's sequential Tasks to use local
completion on prerequisites and a final verdict on the last Task. It does not
require distinct physical entities, add execution relations, select Nodes, or
change Control scheduling. It does not make independent verification a
universal consequence of every physical-world effect. A deployment without
the opt-in may accept a local operation report when that report is the
declared acceptance basis and its actual operation contract establishes the
requested effect. An explicit user or system requirement for independent
confirmation still requires verifier evidence.

## Limits

The policy describes MI admission; it does not prove a verifier source will
produce a verdict. Controller's source and attempt checks remain necessary,
and Habitat remains the official benchmark authority. The current
shared-world verifier is final-only and recognizes a bounded expression
subset. Other deployments need their own explicit source and policy. Existing
accepted plans and historical archives are not rewritten. This decision
does not change MissionPlan v0.8, Formal population admission, benchmark
outcome rules, Core authority, or Local EAIOS action selection.
