//! Presence-aware decoding for nullable Mission timing fields.

use serde::de::Visitor;
use serde::{Deserialize, Deserializer};

/// Required JSON field whose value may explicitly be a millisecond count or null.
pub(super) struct NullableMillis(pub(super) Option<u64>);

impl<'de> Deserialize<'de> for NullableMillis {
    /// Preserves the distinction between an absent contract key and a present null value.
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(NullableMillisVisitor)
    }
}

/// Decodes a present nullable millisecond value without accepting a missing field.
struct NullableMillisVisitor;

impl<'de> Visitor<'de> for NullableMillisVisitor {
    type Value = NullableMillis;

    /// Describes the exact nullable integer contract for Serde diagnostics.
    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("a non-negative millisecond integer or null")
    }

    /// Accepts one present non-negative millisecond count.
    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
        Ok(NullableMillis(Some(value)))
    }

    /// Accepts an explicit JSON null value.
    fn visit_none<E>(self) -> Result<Self::Value, E> {
        Ok(NullableMillis(None))
    }

    /// Accepts the unit representation used by JSON null.
    fn visit_unit<E>(self) -> Result<Self::Value, E> {
        Ok(NullableMillis(None))
    }
}
