//! Actual lifetime and allocation fences, without inventing a network or execution receipt.

use super::*;

/// Complete actual large-pool ownership leaves the separate small-frame reserve usable.
#[test]
fn large_reservations_do_not_borrow_small_capacity_and_drop_releases_original_permits() {
    let budget = TransportBudget::new(ReplicaTransportLimits {
        large_frame_bytes: 1024 * 1024,
        ..Default::default()
    })
    .unwrap();
    let original = budget.frame(1024 * 1024).unwrap();
    assert!(budget.frame(128 * 1024).is_err());
    let small = budget.frame(64 * 1024).unwrap();
    assert_eq!(budget.snapshot().large_frame_active_bytes, 1024 * 1024);
    assert_eq!(budget.snapshot().small_frame_active_bytes, 64 * 1024);
    assert_eq!(budget.snapshot().frame_rejected_total, 1);
    drop(original);
    drop(small);
    assert_eq!(budget.snapshot().large_frame_active_bytes, 0);
    assert_eq!(budget.snapshot().small_frame_active_bytes, 0);
    assert!(budget.frame(0).is_err());
    assert!(budget.frame(MAX_FRAME_BYTES as u64 + 1).is_err());
}

/// Separate actual inbound/outbound permit ownership bounds the complete original operation lifetime.
#[test]
fn connection_admission_never_queues_or_transfers_ownership_between_directions() {
    let budget = TransportBudget::new(ReplicaTransportLimits {
        inbound_connections: 1,
        outbound_connections: 1,
        ..Default::default()
    })
    .unwrap();
    let inbound = budget.inbound().unwrap();
    let outbound = budget.outbound().unwrap();
    assert!(budget.inbound().is_err());
    assert!(budget.outbound().is_err());
    assert_eq!(budget.snapshot().inbound_high_water, 1);
    assert_eq!(budget.snapshot().inbound_rejected_total, 1);
    assert_eq!(budget.snapshot().outbound_rejected_total, 1);
    drop(inbound);
    assert_eq!(budget.snapshot().inbound_active, 0);
    assert_eq!(budget.snapshot().outbound_active, 1);
    drop(outbound);
    assert_eq!(budget.snapshot().outbound_active, 0);
}

/// Actual serialization cannot allocate a large frame when its original process capacity is occupied.
#[test]
fn counted_encoding_retains_exact_permit_and_refuses_full_capacity_before_allocating() {
    let budget = TransportBudget::new(ReplicaTransportLimits {
        large_frame_bytes: 1024 * 1024,
        ..Default::default()
    })
    .unwrap();
    let occupied = budget.frame(1024 * 1024).unwrap();
    let value = "original\n".repeat(32 * 1024);
    assert!(encode(&value, &budget).is_err());
    drop(occupied);
    let actual = encode(&value, &budget).unwrap();
    assert_eq!(
        serde_json::from_slice::<String>(&actual.bytes).unwrap(),
        value
    );
    assert_eq!(
        budget.snapshot().large_frame_active_bytes,
        actual.bytes.len()
    );
    drop(actual);
    assert_eq!(budget.snapshot().large_frame_active_bytes, 0);
    let mut counter = BoundedLength(MAX_FRAME_BYTES);
    assert!(counter.write_all(b"x").is_err());
    let mut buffer = BoundedBuffer {
        bytes: Vec::with_capacity(1),
        limit: 1,
    };
    assert!(buffer.write_all(b"xx").is_err());
    assert!(buffer.bytes.is_empty());
}

/// Closed declarations refuse unbounded or zero resources before storage, tasks or sockets exist.
#[test]
fn declaration_is_closed_and_bounded_without_weakening_the_legacy_frame_cap() {
    let original = ReplicaTransportLimits::default();
    for field in [
        "inbound_connections",
        "outbound_connections",
        "small_frame_bytes",
        "large_frame_bytes",
    ] {
        for value in [0_u64, u64::MAX] {
            let mut candidate = serde_json::to_value(&original).unwrap();
            candidate[field] = value.into();
            let parsed = serde_json::from_value::<ReplicaTransportLimits>(candidate);
            assert!(parsed.is_err() || parsed.unwrap().validate().is_err());
        }
    }
    let mut unknown = serde_json::to_value(&original).unwrap();
    unknown["unbounded"] = true.into();
    assert!(serde_json::from_value::<ReplicaTransportLimits>(unknown).is_err());
    assert_eq!(MAX_FRAME_BYTES, 256 * 1024 * 1024);
}
