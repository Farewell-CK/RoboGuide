//! Bounded network resources and worker handoff without a second application authority.

use ports::{DiagnosticHandle, DiagnosticKind, DiagnosticStage};
use std::future::{Future, poll_fn};
use tokio::sync::Semaphore;

/// Default simultaneous Operator transports, including incomplete TLS and slow responses.
pub(super) const DEFAULT_CONNECTION_LIMIT: usize = 128;

/// Rejects an invalid explicit deployment budget before binding or admitting a connection.
pub(super) fn parse_connection_limit(value: Option<&str>) -> Result<usize, String> {
    value.map_or(Ok(DEFAULT_CONNECTION_LIMIT), |value| {
        value
            .parse::<usize>()
            .ok()
            .filter(|limit| (1..=1024).contains(limit))
            .ok_or_else(|| "Operator connection limit must be an integer in 1..1024".into())
    })
}

/// Returns the network worker before each synchronous poll; Pending retains only the original future.
pub(super) async fn isolate_synchronous_polls<F: Future>(future: F) -> F::Output {
    let mut future = std::pin::pin!(future);
    poll_fn(|context| {
        crate::application::run_synchronous_transaction(|| future.as_mut().poll(context))
    })
    .await
}

/// Samples fixed process-local budgets; permit ownership, not these observations, limits transports.
pub(super) fn observe_connections(
    diagnostics: &DiagnosticHandle,
    permits: &Semaphore,
    limit: usize,
    rejected: u64,
) {
    for (stage, value) in [
        (
            DiagnosticStage::OperatorConnectionsActive,
            limit.saturating_sub(permits.available_permits()) as u64,
        ),
        (DiagnosticStage::OperatorConnectionLimit, limit as u64),
        (DiagnosticStage::OperatorConnectionRejections, rejected),
    ] {
        diagnostics.value(DiagnosticKind::Http, stage, value, None);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// An absent limit retains the default; malformed and out-of-range deployments never bind.
    #[test]
    fn connection_budgets_are_explicit_and_bounded() {
        assert_eq!(parse_connection_limit(None).unwrap(), 128);
        for value in ["0", "1025", "-1", "unbounded", "1.5", ""] {
            assert!(parse_connection_limit(Some(value)).is_err(), "{value}");
        }
        assert_eq!(parse_connection_limit(Some("1")).unwrap(), 1);
        assert_eq!(parse_connection_limit(Some("1024")).unwrap(), 1024);
    }
}
