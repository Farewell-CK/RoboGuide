//! Opt-in bounded warning fields for the actual owned software process probe, never product authority.

use std::fmt::Write as _;
use tracing::span::{Attributes, Id, Record};
use tracing::{Event, Metadata, Subscriber};

/// Records bounded transport warning reasons, excluding spans and arbitrary application fields.
struct WarningSink;

/// Limits each rendered diagnostic without buffering unbounded original application metadata.
struct Fields(String);

impl std::fmt::Write for Fields {
    /// Keeps complete UTF-8 prefixes inside the original per-event diagnostic bound.
    fn write_str(&mut self, text: &str) -> std::fmt::Result {
        let available = 2048usize.saturating_sub(self.0.len());
        let mut end = available.min(text.len());
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        self.0.push_str(&text[..end]);
        Ok(())
    }
}

impl tracing::field::Visit for Fields {
    /// Renders only the upstream warning reason/message, not application content or credentials.
    fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
        if matches!(field.name(), "message" | "error" | "err" | "reason") {
            let _ = write!(self, " {}={value:?}", field.name());
        }
    }
}

impl Subscriber for WarningSink {
    /// Accepts bounded consensus/transport warnings; diagnostics never grant application decisions.
    fn enabled(&self, metadata: &Metadata<'_>) -> bool {
        *metadata.level() <= tracing::Level::WARN
            && (metadata.target().starts_with("openraft")
                || metadata.target().starts_with("controller_replication"))
    }
    /// Ignores span authority; no diagnostic span is interpreted as a process or command identity.
    fn new_span(&self, _span: &Attributes<'_>) -> Id {
        Id::from_u64(1)
    }
    /// Ignores application span fields rather than exporting their payloads.
    fn record(&self, _span: &Id, _values: &Record<'_>) {}
    /// Retains no fabricated cross-span causality.
    fn record_follows_from(&self, _span: &Id, _follows: &Id) {}
    /// Emits one bounded actual upstream warning to the owned probe's retained stderr.
    fn event(&self, event: &Event<'_>) {
        let mut fields = Fields(String::new());
        event.record(&mut fields);
        eprintln!("actual upstream warning:{}", fields.0);
    }
    /// Maintains no implicit application scope from diagnostics.
    fn enter(&self, _span: &Id) {}
    /// Drops no real process or operation scope through diagnostic span exit.
    fn exit(&self, _span: &Id) {}
}

/// Enables bounded upstream diagnostics only when the explicit quality process requests them.
pub(super) fn enable() -> Result<(), String> {
    if std::env::var_os("ROBOGUIDE_REPLICA_PROBE_WARNINGS").is_some() {
        tracing::subscriber::set_global_default(WarningSink).map_err(|error| error.to_string())?;
    }
    Ok(())
}
