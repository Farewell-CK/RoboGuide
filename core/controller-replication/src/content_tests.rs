//! Synthetic DTO counterexamples establish quorum semantics, never deployment or physical facts.

use super::*;
use openraft::{CommittedLeaderId, LogId, Membership};

/// Builds a labelled contract fixture without creating a Node, transport, store or byte receipt.
fn target(groups: Vec<BTreeSet<u64>>) -> ContentQuorumTarget {
    let voters: BTreeSet<_> = groups.iter().flatten().copied().collect();
    let membership = Membership::new(groups, voters.clone());
    ContentQuorumTarget {
        profile_identity: "1".repeat(64),
        epoch: ControllerEpoch {
            term: 1,
            replica_id: 1,
        },
        revision: 2,
        checkpoint_digest: "a".repeat(64),
        batch_digest: "b".repeat(64),
        membership: StoredMembership::new(
            Some(LogId::new(CommittedLeaderId::new(1, 1), 1)),
            membership,
        ),
        member_stores: voters
            .into_iter()
            .map(|id| (id, format!("replica-content:{id:064x}")))
            .collect(),
        inputs: vec![ArtifactContentKey {
            source_owner: format!("artifact-store:{}", "c".repeat(64)),
            content_digest: format!("sha256:{}", "d".repeat(64)),
            byte_size: 10,
        }],
    }
}

/// Supplies labelled complete DTOs solely to isolate quorum/identity counterexamples.
fn observed(target: &ContentQuorumTarget, member: u64) -> MemberContentObservation {
    MemberContentObservation {
        profile_identity: target.profile_identity.clone(),
        member,
        store_owner: target.member_stores[&member].clone(),
        epoch: target.epoch,
        revision: target.revision,
        checkpoint_digest: target.checkpoint_digest.clone(),
        batch_digest: target.batch_digest.clone(),
        membership: target.membership.clone(),
        inputs: target.inputs.clone(),
    }
}

/// Separate byte and metadata majorities do not establish a majority of complete members.
#[test]
fn split_majorities_and_duplicate_replies_cannot_lend_completeness() {
    let target = target(vec![BTreeSet::from([1, 2, 3])]);
    let first = observed(&target, 1);
    let mut second = observed(&target, 2);
    second.inputs.clear();
    let mut third = observed(&target, 3);
    third.batch_digest = "e".repeat(64);
    assert!(
        target
            .complete_members(&[first.clone(), second, third])
            .is_err()
    );
    assert!(
        target
            .complete_members(&[first.clone(), first.clone()])
            .is_err()
    );
    assert_eq!(
        target
            .complete_members(&[first, observed(&target, 3)])
            .unwrap(),
        BTreeSet::from([1, 3])
    );
}

/// Joint configurations require complete majorities in both original groups.
#[test]
fn only_one_joint_majority_never_completes() {
    let target = target(vec![BTreeSet::from([1, 2, 3]), BTreeSet::from([3, 4, 5])]);
    assert!(
        target
            .complete_members(&[
                observed(&target, 1),
                observed(&target, 2),
                observed(&target, 3)
            ])
            .is_err()
    );
    assert_eq!(
        target
            .complete_members(&[
                observed(&target, 1),
                observed(&target, 3),
                observed(&target, 4)
            ])
            .unwrap(),
        BTreeSet::from([1, 3, 4])
    );
}

/// Equal bytes under another original owner cannot replace an omitted source requirement.
#[test]
fn independent_original_owners_and_receiver_instances_remain_distinct() {
    let mut target = target(vec![BTreeSet::from([1, 2, 3])]);
    let mut other = target.inputs[0].clone();
    other.source_owner = format!("artifact-store:{}", "e".repeat(64));
    target.inputs.push(other);
    target.inputs.sort();
    let first = observed(&target, 1);
    let mut missing = observed(&target, 2);
    missing.inputs.pop();
    assert!(target.complete_members(&[first.clone(), missing]).is_err());
    let mut replaced = observed(&target, 2);
    replaced.store_owner = format!("replica-content:{}", "f".repeat(64));
    assert!(target.complete_members(&[first.clone(), replaced]).is_err());
    assert!(
        target
            .complete_members(&[first, observed(&target, 2)])
            .is_ok()
    );
}

/// Stale bindings and conflicting replies exclude that member without discarding independent peers.
#[test]
fn stale_or_conflicting_member_cannot_contribute_a_vote() {
    let target = target(vec![BTreeSet::from([1, 2, 3])]);
    let first = observed(&target, 1);
    let mut stale = observed(&target, 2);
    stale.epoch.term += 1;
    assert!(
        target
            .complete_members(&[first.clone(), stale.clone()])
            .is_err()
    );
    assert_eq!(
        target
            .complete_members(&[first, observed(&target, 2), stale, observed(&target, 3)])
            .unwrap(),
        BTreeSet::from([1, 3])
    );
    let mut foreign = observed(&target, 2);
    foreign.profile_identity = "2".repeat(64);
    assert!(
        target
            .complete_members(&[observed(&target, 1), foreign])
            .is_err()
    );
}

/// Declared voters alone and contradictory original content sizes do not form a valid target.
#[test]
fn absent_committed_membership_and_conflicting_source_sizes_are_refused() {
    let mut target = target(vec![BTreeSet::from([1, 2, 3])]);
    let membership = target.membership.clone();
    target.membership = StoredMembership::new(None, membership.membership().clone());
    assert!(target.validate().is_err());
    target.membership = membership;
    let mut contradictory = target.inputs[0].clone();
    contradictory.byte_size += 1;
    target.inputs.push(contradictory);
    assert!(target.validate().is_err());
}
