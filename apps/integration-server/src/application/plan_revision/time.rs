//! Original same-kernel budgets; wall rollback, process restart and another time domain cannot renew work.

use serde::{Deserialize, Serialize};

/// Actual kernel boot/time namespace and conservative boottime sample, independent of source clocks.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct KernelTime {
    /// Actual Linux boot identity; this is not device identity or attestation.
    boot_id: String,
    /// Actual time namespace inode and filesystem identity.
    namespace: (u64, u64),
    /// Kernel boottime milliseconds, including suspend.
    elapsed_ms: u64,
    /// Conservative resolution of the original proc uptime decimal representation.
    resolution_ms: u64,
}

impl KernelTime {
    /// Reads actual OS evidence; unsupported/changed namespaces fail closed without an epoch fallback.
    pub(super) fn observe() -> Result<Self, String> {
        #[cfg(target_os = "linux")]
        {
            use std::os::unix::fs::MetadataExt;
            let metadata = std::fs::metadata("/proc/self/ns/time").map_err(|e| e.to_string())?;
            let boot_id = std::fs::read_to_string("/proc/sys/kernel/random/boot_id")
                .map_err(|e| e.to_string())?
                .trim()
                .to_string();
            let uptime = std::fs::read_to_string("/proc/uptime").map_err(|e| e.to_string())?;
            let value = uptime
                .split_whitespace()
                .next()
                .ok_or("kernel uptime is absent")?;
            let (seconds, fraction) = value
                .split_once('.')
                .ok_or("kernel uptime has no explicit resolution")?;
            if boot_id.len() != 36
                || !boot_id
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit() || byte == b'-')
                || fraction.is_empty()
                || fraction.len() > 9
                || !fraction.bytes().all(|byte| byte.is_ascii_digit())
            {
                return Err("invalid actual kernel time evidence".into());
            }
            let seconds = seconds.parse::<u64>().map_err(|e| e.to_string())?;
            let millis = format!("{fraction:0<3}")[..3]
                .parse::<u64>()
                .map_err(|e| e.to_string())?;
            let elapsed_ms = seconds
                .checked_mul(1000)
                .and_then(|seconds| seconds.checked_add(millis))
                .ok_or("kernel time overflow")?;
            let resolution_ms = match fraction.len() {
                1 => 100,
                2 => 10,
                _ => 1,
            };
            let after = std::fs::metadata("/proc/self/ns/time").map_err(|e| e.to_string())?;
            if (metadata.dev(), metadata.ino()) != (after.dev(), after.ino()) {
                return Err("time namespace changed during observation".into());
            }
            Ok(Self {
                boot_id,
                namespace: (metadata.dev(), metadata.ino()),
                elapsed_ms,
                resolution_ms,
            })
        }
        #[cfg(not(target_os = "linux"))]
        Err(
            "runtime plan revision requires an explicit supported original kernel clock profile"
                .into(),
        )
    }
}

/// One nonrenewable original receive budget; another kernel/namespace or regressed time is unavailable.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct OriginalBudget {
    /// Actual original local clock sample retained across process restart.
    issued: KernelTime,
    /// Original bounded duration; no restoration path changes it.
    duration_ms: u64,
}

impl OriginalBudget {
    /// Returns the immutable declared duration for cross-authority restore validation.
    pub(super) fn duration_ms(&self) -> u64 {
        self.duration_ms
    }
    /// Validates retained original OS evidence without treating another current domain as a renewal.
    pub(super) fn validate(&self) -> Result<(), String> {
        if self.duration_ms == 0
            || self.duration_ms > 3_600_000
            || self.issued.boot_id.len() != 36
            || !self
                .issued
                .boot_id
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() || byte == b'-')
            || self.issued.namespace.1 == 0
            || !matches!(self.issued.resolution_ms, 1 | 10 | 100)
        {
            return Err("invalid retained original kernel budget".into());
        }
        Ok(())
    }
    /// Admits a positive bounded duration from actual current kernel evidence.
    pub(super) fn new(now: &KernelTime, duration_ms: u64) -> Result<Self, String> {
        if duration_ms == 0 || duration_ms > 3_600_000 {
            return Err("invalid original revision budget".into());
        }
        Ok(Self {
            issued: now.clone(),
            duration_ms,
        })
    }

    /// Returns a conservative remainder without refreshing its original anchor or duration.
    pub(super) fn remaining(&self, now: &KernelTime) -> u64 {
        if self.duration_ms == 0
            || self.duration_ms > 3_600_000
            || self.issued.boot_id != now.boot_id
            || self.issued.namespace != now.namespace
            || now.elapsed_ms < self.issued.elapsed_ms
            || !(1..=100).contains(&self.issued.resolution_ms)
            || !(1..=100).contains(&now.resolution_ms)
        {
            return 0;
        }
        self.duration_ms
            .saturating_sub(now.elapsed_ms - self.issued.elapsed_ms)
            .saturating_sub(self.issued.resolution_ms.max(now.resolution_ms))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Real OS sampling plus durable serialization cannot refresh the original deadline or namespace.
    #[test]
    fn original_kernel_budget_survives_serialization_and_rejects_domain_change() {
        let actual = KernelTime::observe().unwrap();
        let budget = OriginalBudget::new(&actual, 1000).unwrap();
        let original = serde_json::to_string(&budget).unwrap();
        let restored: OriginalBudget = serde_json::from_str(&original).unwrap();
        assert!(restored.remaining(&KernelTime::observe().unwrap()) <= 1000 - actual.resolution_ms);
        let mut later = actual.clone();
        later.elapsed_ms += 1001;
        assert_eq!(restored.remaining(&later), 0);
        let mut changed = actual.clone();
        changed.namespace.1 += 1;
        assert_eq!(restored.remaining(&changed), 0);
        changed = actual.clone();
        changed.boot_id = "another-original-boot".into();
        assert_eq!(restored.remaining(&changed), 0);
        assert_eq!(serde_json::to_string(&restored).unwrap(), original);
    }
}
