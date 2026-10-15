//! Ephemeral current-session routing context without changing durable invocation identity.

use super::*;

/// Clears only the accepted route owned by this connection when it ends or its future is dropped.
pub(crate) struct PublicationSessionGuard {
    /// Shared engine state; no transport owns the immutable invocation journal.
    inner: Arc<EngineInner>,
    /// Exact accepted route belonging to this guard.
    session_id: String,
}

impl Drop for PublicationSessionGuard {
    /// Fences new publication through a dead route, without erasing a newer accepted session.
    fn drop(&mut self) {
        if let Ok(mut current) = self.inner.publication_session.lock()
            && current.as_deref() == Some(self.session_id.as_str())
        {
            *current = None;
        }
    }
}

impl LocalIntegrationEngine {
    /// Attaches an actually accepted Node route; request parameters cannot choose this session.
    pub(crate) fn publication_session(
        &self,
        session_id: &str,
    ) -> Result<PublicationSessionGuard, EngineError> {
        if session_id.is_empty()
            || session_id.len() > 1024
            || session_id.chars().any(char::is_control)
        {
            return Err(EngineError::Protocol(
                "accepted publication session is invalid".into(),
            ));
        }
        *self
            .inner
            .publication_session
            .lock()
            .map_err(|_| EngineError::LockState)? = Some(session_id.into());
        Ok(PublicationSessionGuard {
            inner: Arc::clone(&self.inner),
            session_id: session_id.into(),
        })
    }
}
