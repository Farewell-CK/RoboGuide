//! Actual same-root source/archive copier fence; it never sends a Node command or deletes partial bytes.

use super::*;
use state::{
    ArchiveAbortIntent, ArchiveAbortReceipt, ArchiveAbortSink, ArchiveCopierFence,
    ArchiveLeaseIdentity,
};

/// Original durable cleanup evidence, independent of final publication and source-side SQL receipts.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Tombstone {
    /// Exact supported local cleanup meaning, never a device stop certificate.
    schema: String,
    /// Complete original cleanup command, source and nonrenewable budget.
    intent: ArchiveAbortIntent,
    /// Actual original writer file descriptions and current process incarnation.
    fence: ArchiveCopierFence,
}

/// Treats dangling symbolic links and corrupt files as existing evidence, never as no publication.
fn exists(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.to_string()),
    }
}

/// Reads actual current Linux kernel time and Controller incarnation without wall-clock fallback.
fn process_sample() -> Result<(String, String, u64, u32, u64), String> {
    let boot = fs::read_to_string("/proc/sys/kernel/random/boot_id")
        .map_err(|error| error.to_string())?
        .trim()
        .to_owned();
    let namespace = fs::read_link("/proc/self/ns/time")
        .map_err(|error| error.to_string())?
        .to_string_lossy()
        .into_owned();
    let uptime = fs::read_to_string("/proc/uptime").map_err(|error| error.to_string())?;
    let (seconds, fraction) = uptime
        .split_whitespace()
        .next()
        .and_then(|value| value.split_once('.'))
        .ok_or("actual cleanup kernel uptime is unavailable")?;
    if boot.len() != 36
        || namespace.len() > 128
        || fraction.len() < 2
        || !fraction.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err("actual cleanup kernel identity is invalid".into());
    }
    let millis = seconds
        .parse::<u64>()
        .map_err(|error| error.to_string())?
        .checked_mul(1000)
        .and_then(|value| {
            format!("{fraction:0<3}")[..3]
                .parse::<u64>()
                .ok()
                .and_then(|millis| value.checked_add(millis))
        })
        .ok_or("actual cleanup kernel uptime overflow")?;
    let pid = std::process::id();
    let raw = fs::read_to_string("/proc/self/stat").map_err(|error| error.to_string())?;
    let (header, tail) = raw
        .rsplit_once(')')
        .ok_or("actual Controller process stat is incomplete")?;
    if !header.starts_with(&format!("{pid} (")) {
        return Err("actual Controller process stat names another incarnation".into());
    }
    let start = tail
        .split_whitespace()
        .nth(19)
        .ok_or("actual Controller start tick is unavailable")?
        .parse::<u64>()
        .map_err(|error| error.to_string())?;
    if start == 0 {
        return Err("actual Controller start tick is invalid".into());
    }
    Ok((boot, namespace, millis, pid, start))
}

impl ContentArchiveSink {
    /// Checks the first cleanup budget, independently of the unchanged original copy grant.
    fn abort_permit(&self, intent: &ArchiveAbortIntent) -> Result<(), String> {
        intent.require_budget()?;
        (self.authorize)()
    }

    /// Observes actual original file descriptions while both source and Archive writers remain held.
    fn copier_fence(
        &self,
        original: &ArchiveIntent,
        intent: &ArchiveAbortIntent,
    ) -> Result<ArchiveCopierFence, String> {
        intent.validate(original)?;
        self.abort_permit(intent)?;
        let source = self
            .source
            .writer_identity()
            .map_err(|error| error.to_string())?;
        if source.owner_id != intent.source_owner {
            return Err("cleanup source writer belongs to another original owner".into());
        }
        #[cfg(unix)]
        let archive_writer = {
            use std::os::unix::fs::MetadataExt;
            let held = self
                .lease
                .lock()
                .map_err(|_| "actual Archive lease is poisoned")?;
            let Some((file, true)) = &*held else {
                return Err("cleanup requires the actual exclusive original Archive writer".into());
            };
            let actual = file.metadata().map_err(|error| error.to_string())?;
            let path = Path::new(&intent.destination).join(".archive-writer.lock");
            let named = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
            if !actual.is_file()
                || !named.file_type().is_file()
                || (actual.dev(), actual.ino()) != (named.dev(), named.ino())
                || actual.ino() == 0
            {
                return Err("actual original Archive writer description was replaced".into());
            }
            ArchiveLeaseIdentity {
                device: actual.dev(),
                inode: actual.ino(),
            }
        };
        #[cfg(not(unix))]
        let archive_writer: ArchiveLeaseIdentity =
            return Err("actual Archive cleanup requires a supported OS writer profile".into());
        let (boot_id, time_namespace, observed_ms, process_id, process_start_ticks) =
            process_sample()?;
        let fence = ArchiveCopierFence {
            schema: "roboguide.original-local-archive-copier-fence/v0.1".into(),
            source_owner: source.owner_id,
            source_writer: ArchiveLeaseIdentity {
                device: source.lease_device,
                inode: source.lease_inode,
            },
            archive_writer,
            process_id,
            process_start_ticks,
            boot_id,
            time_namespace,
            observed_ms,
        };
        self.abort_permit(intent)?;
        Ok(fence)
    }

    /// Names only the exact first cleanup command beneath its original copy directory.
    fn tombstone_path(
        &self,
        original: &ArchiveIntent,
        intent: &ArchiveAbortIntent,
    ) -> Result<PathBuf, String> {
        intent.validate(original)?;
        Ok(self.directory(original)?.join(format!(
            "abort-{:x}.json",
            Sha256::digest(intent.request.command_id.as_bytes())
        )))
    }

    /// Reads and syncs original tombstone evidence under newly held actual fences, without new copy effects.
    fn inspect_abort(
        &self,
        original: &ArchiveIntent,
        intent: &ArchiveAbortIntent,
    ) -> Result<ArchiveAbortReceipt, String> {
        let started = std::time::Instant::now();
        (self.authorize)()?;
        intent.validate(original)?;
        self.acquire_archive_lock(original, true, false, &|| {
            if started.elapsed().as_secs() >= 30 {
                return Err("original cleanup observation exceeded its read budget".into());
            }
            (self.authorize)()
        })?;
        let source = self
            .source
            .writer_identity()
            .map_err(|error| error.to_string())?;
        if source.owner_id != intent.source_owner {
            return Err("original cleanup source owner changed".into());
        }
        let directory = self.directory(original)?;
        if exists(&directory.join("publication.json"))? {
            return Err(
                "published Archive cannot be aborted; reconcile its original publication".into(),
            );
        }
        let path = self.tombstone_path(original, intent)?;
        let actual = if exists(&path)? {
            path
        } else {
            path.with_extension("pending")
        };
        let raw = read(&actual, 16 * 1024)?;
        let tombstone: Tombstone =
            serde_json::from_slice(&raw).map_err(|error| error.to_string())?;
        if tombstone.schema != "roboguide.archive-abort-tombstone/v0.1"
            || &tombstone.intent != intent
            || raw != canonical(&tombstone)?
        {
            return Err("original cleanup tombstone input changed".into());
        }
        let receipt = ArchiveAbortReceipt {
            schema: "roboguide.archive-abort-receipt/v0.1".into(),
            abort_intent_digest: intent.digest()?,
            tombstone_digest: digest(&raw),
            fence: tombstone.fence,
        };
        receipt.validate(intent)?;
        (self.authorize)()?;
        if started.elapsed().as_secs() >= 30 {
            return Err("original cleanup observation exceeded its read budget".into());
        }
        open_regular(&actual)?
            .sync_all()
            .map_err(|error| error.to_string())?;
        File::open(&directory)
            .and_then(|file| file.sync_all())
            .map_err(|error| error.to_string())?;
        Ok(receipt)
    }
}

impl ArchiveAbortSink for ContentArchiveSink {
    /// Persists original cleanup evidence only after actual source/archive writers exclude the copier.
    fn abort_unpublished(
        &self,
        original: &ArchiveIntent,
        intent: &ArchiveAbortIntent,
    ) -> Result<ArchiveAbortReceipt, String> {
        self.abort_permit(intent)?;
        intent.validate(original)?;
        self.acquire_archive_lock(original, true, true, &|| self.abort_permit(intent))?;
        let directory = self.directory(original)?;
        if exists(&directory.join("publication.json"))? {
            return Err(
                "published Archive requires original publication reconciliation, never abort"
                    .into(),
            );
        }
        let fence = self.copier_fence(original, intent)?;
        create_directory(&directory)?;
        if exists(&directory.join("intent.json"))?
            && read(&directory.join("intent.json"), MAX_METADATA)? != canonical(original)?
        {
            return Err("partial Archive differs from its original retained copy intent".into());
        }
        self.abort_permit(intent)?;
        let path = self.tombstone_path(original, intent)?;
        if exists(&path)? || exists(&path.with_extension("pending"))? {
            return self.inspect_abort(original, intent);
        }
        publish(
            &path,
            &canonical(&Tombstone {
                schema: "roboguide.archive-abort-tombstone/v0.1".into(),
                intent: intent.clone(),
                fence,
            })?,
        )?;
        self.inspect_abort(original, intent)
    }

    /// Rechecks original durable evidence and actual exclusive fences without renewing an effect budget.
    fn observe_abort(
        &self,
        original: &ArchiveIntent,
        intent: &ArchiveAbortIntent,
    ) -> Result<ArchiveAbortReceipt, String> {
        self.inspect_abort(original, intent)
    }

    /// Keeps the actual original transport grant current until source-pin retirement Commit.
    fn require_commit_permission(&self) -> Result<(), String> {
        (self.authorize)()
    }
}
