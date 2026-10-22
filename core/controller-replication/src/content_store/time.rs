//! Actual Linux same-kernel receive budget; restart and serialization never renew its original clock.

use serde::{Deserialize, Serialize};
use std::os::unix::fs::MetadataExt;

/// Original member-local clock and actual process evidence, never a distributed time or device claim.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ContentBudget {
    /// Actual original Linux boot identity.
    boot_id: String,
    /// Actual original time namespace device/inode.
    namespace: (u64, u64),
    /// Actual original PID namespace; another container's same-number PID cannot prove absence.
    pid_namespace: (u64, u64),
    /// Original receive time in this actual kernel namespace.
    admitted_ms: u64,
    /// Immutable bounded duration, including original queue and I/O time.
    pub(super) duration_ms: u64,
    /// Original receiver process, independently of a future process's PID.
    pid: u32,
    /// Actual original kernel process start ticks.
    start_ticks: u64,
}

/// Actual negative original-process observation under the original content-store leases.
/// This proves only that the original software incarnation cannot continue local I/O.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentProcessEndObservation {
    /// Original actual kernel boot, retained independently of the observer's current boot.
    original_boot_id: String,
    /// Original actual receiving/reading process.
    original_pid: u32,
    /// Original actual Linux process start ticks, preventing PID reuse from borrowing identity.
    original_start_ticks: u64,
    /// Actual observing process/kernel sample; never an original OS wait or device-stop receipt.
    observer: ContentBudget,
    /// Actual negative observation: new kernel, absent original process, or reused PID.
    reason: String,
    /// Actual replacement start ticks only when the original PID has been reused.
    replacement_start_ticks: Option<u64>,
}

/// Reads actual Linux start ticks; inaccessible or malformed process records never prove exit.
fn process_ticks(pid: u32) -> Result<Option<u64>, String> {
    let raw = match std::fs::read_to_string(format!("/proc/{pid}/stat")) {
        Ok(raw) => raw,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    raw.rsplit_once(')')
        .and_then(|(_, tail)| tail.split_whitespace().nth(19))
        .ok_or("actual content process start ticks unavailable")?
        .parse::<u64>()
        .map(Some)
        .map_err(|error| error.to_string())
}

/// Samples actual kernel uptime and namespace without substituting wall-clock or caller evidence.
fn sample() -> Result<(String, (u64, u64), u64), String> {
    let boot = std::fs::read_to_string("/proc/sys/kernel/random/boot_id")
        .map_err(|error| error.to_string())?
        .trim()
        .to_string();
    let namespace = std::fs::metadata("/proc/self/ns/time").map_err(|error| error.to_string())?;
    let uptime = std::fs::read_to_string("/proc/uptime").map_err(|error| error.to_string())?;
    let original = uptime
        .split_whitespace()
        .next()
        .ok_or("actual kernel uptime is unavailable")?;
    let (seconds, fraction) = original
        .split_once('.')
        .ok_or("actual kernel uptime is invalid")?;
    if fraction.is_empty() || !fraction.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err("actual kernel uptime resolution is invalid".into());
    }
    let seconds: u64 = seconds.parse::<u64>().map_err(|error| error.to_string())?;
    let fraction = format!("{:0<3}", fraction.chars().take(3).collect::<String>())
        .parse::<u64>()
        .map_err(|error| error.to_string())?;
    let millis = seconds
        .checked_mul(1000)
        .and_then(|value| value.checked_add(fraction))
        .ok_or("actual kernel uptime overflow")?;
    Ok((boot, (namespace.dev(), namespace.ino()), millis))
}

impl ContentBudget {
    /// Admits one original positive receive duration before it can wait for the metadata writer.
    pub(super) fn issue(duration_ms: u64) -> Result<Self, String> {
        if !(100..=300_000).contains(&duration_ms) {
            return Err("content receive budget is outside its explicit bound".into());
        }
        let (boot_id, namespace, admitted_ms) = sample()?;
        let pid_namespace =
            std::fs::metadata("/proc/self/ns/pid").map_err(|error| error.to_string())?;
        let ticks =
            process_ticks(std::process::id())?.ok_or("actual content process disappeared")?;
        Ok(Self {
            boot_id,
            namespace,
            pid_namespace: (pid_namespace.dev(), pid_namespace.ino()),
            admitted_ms,
            duration_ms,
            pid: std::process::id(),
            start_ticks: ticks,
        })
    }

    /// Checks retained original shape without fabricating a current process or renewing its deadline.
    pub(super) fn validate(&self) -> Result<(), String> {
        if self.boot_id.len() != 36
            || !self
                .boot_id
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() || byte == b'-')
            || self.namespace.1 == 0
            || self.pid_namespace.1 == 0
            || self.pid == 0
            || self.start_ticks == 0
            || !(100..=300_000).contains(&self.duration_ms)
            || self.admitted_ms.checked_add(self.duration_ms).is_none()
        {
            return Err("invalid original content receive kernel/process budget".into());
        }
        Ok(())
    }

    /// Refuses effects after the original same-kernel deadline, conservatively including resolution.
    pub(super) fn check(&self) -> Result<u64, String> {
        self.validate()?;
        let (boot, namespace, now) = sample()?;
        if boot != self.boot_id
            || namespace != self.namespace
            || now < self.admitted_ms
            || now.saturating_sub(self.admitted_ms).saturating_add(10) >= self.duration_ms
        {
            return Err("original content receive budget expired or changed kernel".into());
        }
        Ok(now)
    }

    /// Validates the retained actual decision sample without comparing it with a new clock.
    pub(super) fn validates_completion(&self, completed_ms: u64) -> bool {
        self.validate().is_ok()
            && completed_ms >= self.admitted_ms
            && completed_ms
                .saturating_sub(self.admitted_ms)
                .saturating_add(10)
                < self.duration_ms
    }

    /// Samples closure of this actual original process's handle, even after its read budget expired.
    /// Closure never authorizes further reads or renews the original I/O deadline.
    pub(super) fn closed_now(&self) -> Result<u64, String> {
        let actual = Self::issue(300_000)?;
        if (
            self.boot_id.as_str(),
            self.namespace,
            self.pid_namespace,
            self.pid,
            self.start_ticks,
        ) != (
            actual.boot_id.as_str(),
            actual.namespace,
            actual.pid_namespace,
            actual.pid,
            actual.start_ticks,
        ) || actual.admitted_ms < self.admitted_ms
        {
            return Err("original content handle close belongs to another process/kernel".into());
        }
        Ok(actual.admitted_ms)
    }

    /// Checks retained original close time without asserting that payload I/O stayed within budget.
    pub(super) fn validates_close(&self, closed_ms: u64) -> bool {
        self.validate().is_ok() && closed_ms >= self.admitted_ms
    }

    /// Independently observes actual original process absence; elapsed time is never exit proof.
    pub(super) fn observe_process_end(&self) -> Result<ContentProcessEndObservation, String> {
        self.validate()?;
        let observer = Self::issue(300_000)?;
        let (reason, replacement_start_ticks) = if observer.boot_id != self.boot_id {
            ("new-kernel", None)
        } else {
            if observer.pid_namespace != self.pid_namespace {
                return Err(
                    "original content process cannot be observed from another PID namespace".into(),
                );
            }
            match process_ticks(self.pid)? {
                None => ("original-process-absent", None),
                Some(ticks) if ticks != self.start_ticks => ("pid-reused", Some(ticks)),
                Some(_) => {
                    return Err("original content process incarnation is still present".into());
                }
            }
        };
        Ok(ContentProcessEndObservation {
            original_boot_id: self.boot_id.clone(),
            original_pid: self.pid,
            original_start_ticks: self.start_ticks,
            observer,
            reason: reason.into(),
            replacement_start_ticks,
        })
    }

    /// Validates immutable retained negative evidence, without turning it into an OS wait receipt.
    pub(super) fn validates_end(&self, evidence: &ContentProcessEndObservation) -> bool {
        if self.validate().is_err()
            || evidence.observer.validate().is_err()
            || (
                evidence.original_boot_id.as_str(),
                evidence.original_pid,
                evidence.original_start_ticks,
            ) != (self.boot_id.as_str(), self.pid, self.start_ticks)
        {
            return false;
        }
        match evidence.reason.as_str() {
            "new-kernel" => {
                evidence.observer.boot_id != self.boot_id
                    && evidence.replacement_start_ticks.is_none()
            }
            "original-process-absent" => {
                evidence.observer.boot_id == self.boot_id
                    && evidence.observer.pid_namespace == self.pid_namespace
                    && evidence.replacement_start_ticks.is_none()
            }
            "pid-reused" => {
                evidence.observer.boot_id == self.boot_id
                    && evidence.observer.pid_namespace == self.pid_namespace
                    && evidence
                        .replacement_start_ticks
                        .is_some_and(|ticks| ticks > 0 && ticks != self.start_ticks)
            }
            _ => false,
        }
    }
}
