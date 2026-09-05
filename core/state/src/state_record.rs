//! Rebuildable projection of independently attributed State records.

use domain::{EventPayload, EventRecord, StateRecord, StateRecordKey};
use ports::{StateRecordError, StateRecordReader, StateRecordWriter};
use std::cmp::Ordering;
use std::collections::BTreeMap;

/// Latest record per exact object/semantic/source/channel key.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct StateRecordProjection {
    /// Independently attributed channels in deterministic identity order.
    records: BTreeMap<StateRecordKey, StateRecord>,
}

impl StateRecordProjection {
    /// Creates an empty source-aware State projection.
    pub const fn new() -> Self {
        Self {
            records: BTreeMap::new(),
        }
    }

    /// Restores a projection by replaying State events and ignoring unrelated evidence.
    pub fn from_events<I>(events: I) -> Result<Self, StateRecordError>
    where
        I: IntoIterator<Item = EventRecord>,
    {
        let mut projection = Self::new();
        for event in events {
            if matches!(event.payload(), EventPayload::StateRecordObserved { .. }) {
                projection.apply_state_event(&event)?;
            }
        }
        Ok(projection)
    }

    /// Returns owned records for checkpoint persistence in deterministic order.
    pub fn snapshots(&self) -> Vec<StateRecord> {
        self.records.values().cloned().collect()
    }

    /// Restores checkpoint records through the same ordering invariants as live ingestion.
    pub fn restore(records: Vec<StateRecord>) -> Result<Self, StateRecordError> {
        let mut projection = Self::new();
        for record in records {
            projection.record_state(record)?;
        }
        Ok(projection)
    }
}

impl StateRecordReader for StateRecordProjection {
    /// Returns an owned latest record for one exact key.
    fn record(&self, key: &StateRecordKey) -> Option<StateRecord> {
        self.records.get(key).cloned()
    }

    /// Returns every independent latest record in deterministic key order.
    fn records(&self) -> Vec<StateRecord> {
        self.snapshots()
    }
}

impl StateRecordWriter for StateRecordProjection {
    /// Records one channel update without merging independent sources.
    fn record_state(&mut self, record: StateRecord) -> Result<(), StateRecordError> {
        if let Some(current) = self.records.get(record.key()) {
            let ordering = record
                .received_at()
                .cmp(&current.received_at())
                .then_with(|| {
                    if record.source_epoch() != current.source_epoch() {
                        // Transport has already fenced old sessions. At the same receive millisecond,
                        // replay/application order therefore makes a different incoming epoch newer.
                        Ordering::Greater
                    } else {
                        record.sequence().cmp(&current.sequence())
                    }
                });
            match ordering {
                Ordering::Less => {
                    return Err(StateRecordError::StaleRecord(format!(
                        "{} arrived at {}ms sequence {} after {}ms sequence {}",
                        record.key().channel_id(),
                        record.received_at().as_millis(),
                        record.sequence(),
                        current.received_at().as_millis(),
                        current.sequence()
                    )));
                }
                Ordering::Equal if current != &record => {
                    return Err(StateRecordError::ConflictingRecord(format!(
                        "{} reused receive time and sequence",
                        record.key().channel_id()
                    )));
                }
                Ordering::Equal => return Ok(()),
                Ordering::Greater => {}
            }
        }
        self.records.insert(record.key().clone(), record);
        Ok(())
    }

    /// Applies one State event envelope.
    fn apply_state_event(&mut self, event: &EventRecord) -> Result<(), StateRecordError> {
        match event.payload() {
            EventPayload::StateRecordObserved { record } => self.record_state(record.clone()),
            _ => Err(StateRecordError::UnsupportedEvent),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        LocalSystemId, NodeId, StateObjectClass, StateObjectRef, StateSemantic, StateSource,
        TimestampMs,
    };

    /// Builds one record for projection ordering tests.
    fn record(node: &str, received_at: u64, sequence: u64, value: bool) -> StateRecord {
        StateRecord::new(
            StateObjectRef::new(StateObjectClass::World, "hazard", "crossing-a")
                .expect("object should be valid"),
            StateSemantic::Observed,
            StateSource::Node {
                node_id: NodeId::new(node).expect("node should be valid"),
                local_system_id: LocalSystemId::new("safety").expect("system should be valid"),
            },
            "hazards",
            "example.hazard/v1",
            serde_json::json!({"present": value}),
            None,
            TimestampMs::new(received_at),
            1_000,
            None,
            sequence,
        )
        .expect("record should be valid")
    }

    /// Independent sources coexist instead of being collapsed into global truth.
    #[test]
    fn independent_sources_coexist() {
        let mut projection = StateRecordProjection::new();
        projection
            .record_state(record("cane-a", 10, 1, true))
            .expect("first source should apply");
        projection
            .record_state(record("dog-a", 11, 1, false))
            .expect("second source should apply");
        assert_eq!(projection.records().len(), 2);
    }

    /// Older RoboGuide receive time cannot replace newer channel evidence.
    #[test]
    fn older_receive_time_is_rejected() {
        let mut projection = StateRecordProjection::new();
        projection
            .record_state(record("cane-a", 20, 2, false))
            .expect("new record should apply");
        assert!(matches!(
            projection.record_state(record("cane-a", 10, 99, true)),
            Err(StateRecordError::StaleRecord(_))
        ));
    }

    /// A reconnect epoch may restart its sequence without being rejected in the same millisecond.
    #[test]
    fn reconnect_epoch_disambiguates_equal_receive_time() {
        let mut projection = StateRecordProjection::new();
        let first = StateRecord::new_with_source_epoch(
            StateObjectRef::new(StateObjectClass::World, "hazard", "crossing-a")
                .expect("object should be valid"),
            StateSemantic::Observed,
            StateSource::Node {
                node_id: NodeId::new("cane-a").expect("node should be valid"),
                local_system_id: LocalSystemId::new("safety").expect("system should be valid"),
            },
            "hazards",
            "example.hazard/v1",
            serde_json::json!({"present": false}),
            None,
            TimestampMs::new(20),
            1_000,
            None,
            Some("session-old".to_string()),
            99,
        )
        .expect("old session record should be valid");
        let second = StateRecord::new_with_source_epoch(
            first.key().object().clone(),
            first.key().semantic(),
            first.key().source().clone(),
            first.key().channel_id(),
            first.payload_schema(),
            serde_json::json!({"present": true}),
            None,
            TimestampMs::new(20),
            1_000,
            None,
            Some("session-new".to_string()),
            1,
        )
        .expect("new session record should be valid");

        projection
            .record_state(first)
            .expect("old session record should apply");
        projection
            .record_state(second.clone())
            .expect("new session record should replace it");
        assert_eq!(projection.records(), vec![second]);
    }

    /// Restore preserves receive time so an expired record cannot become fresh after restart.
    #[test]
    fn restore_preserves_receive_time_for_freshness() {
        let record = record("cane-a", 10, 1, true);
        let projection = StateRecordProjection::restore(vec![record.clone()])
            .expect("checkpoint record should restore");
        let restored = &projection.records()[0];
        assert_eq!(restored.received_at(), record.received_at());
        assert!(restored.is_stale_at(TimestampMs::new(1_011)));
        assert!(!restored.is_stale_at(TimestampMs::new(1_009)));
    }
}
