//! Private-candidate event staging; durable acceptance remains the caller's original transaction.

use crate::EventSink;
use domain::{CorrelationId, EventId, EventPayload, TimestampMs};

/// One original append, retained until all candidate validation succeeds.
struct PendingEvent {
    /// Original decision receive time, never refreshed by publication.
    timestamp: TimestampMs,
    /// Original operation trace.
    correlation: CorrelationId,
    /// Original preceding event, if one was supplied.
    causation: Option<EventId>,
    /// Complete typed evidence.
    payload: EventPayload,
}

/// Transient evidence for one input-bounded candidate; dropping it publishes nothing.
#[derive(Default)]
pub struct PendingEvents(Vec<PendingEvent>);

impl EventSink for PendingEvents {
    /// Stages the exact append; callers retain their existing validated input/transaction bounds.
    fn append(
        &mut self,
        timestamp: TimestampMs,
        correlation: &CorrelationId,
        causation: Option<&EventId>,
        payload: EventPayload,
    ) {
        self.0.push(PendingEvent {
            timestamp,
            correlation: correlation.clone(),
            causation: causation.cloned(),
            payload,
        });
    }
}

impl PendingEvents {
    /// Forwards original evidence in order; this never commits a checkpoint or grants authority.
    pub fn publish(self, sink: &mut impl EventSink) {
        for event in self.0 {
            sink.append(
                event.timestamp,
                &event.correlation,
                event.causation.as_ref(),
                event.payload,
            );
        }
    }
}
