//! Actual retained-owner Archive publication and independent readonly original-result reconciliation.

use artifact_store::{ArtifactReadOnlyStore, FileSystemArtifactStore};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use state::{ArchiveIntent, ArchiveManifest, ArchiveSink, PersistedCheckpoint, PersistedEvent};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// Closed metadata and retained original checkpoint ceiling, independently of bounded CAS streaming.
const MAX_METADATA: u64 = 128 * 1024 * 1024;
/// Maximum one-copy working set and current-authority recheck interval.
const CHUNK_BYTES: usize = 1024 * 1024;
/// Complete archive contract; an immutable file alone never grants application authority.
const SCHEMA: &str = "roboguide.content-archive/v0.1";

/// Receiving content reference, retained independently of source-side SQL compaction.
#[derive(Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct RecipientReference {
    /// Explicit original receiving responsibility, never a Snapshot or read grant.
    schema: String,
    /// Complete original source command, content and nonrenewing budget identity.
    intent: ArchiveIntent,
    /// Actual receiving CAS software identity fixed before the first source byte read.
    target_owner: String,
}

/// Independently hashable original publication, binding the actual target software owner before reads.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Publication {
    /// Exact supported archive interpretation.
    schema: String,
    /// Complete first original command, source, checkpoint, requirements and kernel budget.
    intent: ArchiveIntent,
    /// Actual OS-generated receiving CAS identity, independent of the original source owner.
    target_owner: String,
    /// Full original checkpoint envelope bytes; payload spelling remains unchanged.
    checkpoint_digest: String,
}

/// Actual application adapter; the caller holds the source application writer for its lifetime.
pub(crate) struct ContentArchiveSink {
    /// Original writer-owned source CAS; clones preserve that same root lease.
    source: FileSystemArtifactStore,
    /// Current exact request witness; called during effects and before accepting publication proof.
    authorize: Arc<dyn Fn() -> Result<(), String> + Send + Sync>,
    /// Original global Archive root lease, retained through the source SQL maintenance Commit.
    lease: Mutex<Option<(File, bool)>>,
}

/// Produces a stable full JSON identity without reformatting original nested payload strings.
fn canonical<T: Serialize + ?Sized>(value: &T) -> Result<Vec<u8>, String> {
    serde_json::to_vec(&serde_json::to_value(value).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())
}

/// Hashes exact input bytes, never a declared digest or target filename alone.
fn digest(bytes: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(bytes))
}

/// Requires actual directories at every ancestor before traversing immutable archive files.
fn real_directory(path: &Path) -> Result<(), String> {
    if !path.is_absolute()
        || path
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
    {
        return Err("Archive requires an unambiguous absolute deployment directory".into());
    }
    for directory in path.ancestors() {
        if !fs::symlink_metadata(directory)
            .map_err(|error| error.to_string())?
            .file_type()
            .is_dir()
        {
            return Err("Archive directory cannot contain symlinks or non-directories".into());
        }
    }
    Ok(())
}

/// Creates missing actual directories and durably publishes each entry without following symlinks.
fn create_directory(path: &Path) -> Result<(), String> {
    if fs::symlink_metadata(path).is_ok() {
        return real_directory(path);
    }
    let parent = path.parent().ok_or("Archive directory has no parent")?;
    create_directory(parent)?;
    fs::create_dir(path).map_err(|error| error.to_string())?;
    File::open(parent)
        .and_then(|file| file.sync_all())
        .map_err(|error| error.to_string())?;
    real_directory(path)
}

/// Opens an existing regular leaf without following a symlink; no file or lease is created.
fn open_regular(path: &Path) -> Result<File, String> {
    real_directory(path.parent().ok_or("Archive file parent unavailable")?)?;
    if !fs::symlink_metadata(path)
        .map_err(|error| error.to_string())?
        .file_type()
        .is_file()
    {
        return Err("Archive observation requires a regular original file".into());
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(0x20000);
    }
    let file = options.open(path).map_err(|error| error.to_string())?;
    if !file
        .metadata()
        .map_err(|error| error.to_string())?
        .is_file()
    {
        return Err("Archive original file changed type".into());
    }
    Ok(file)
}

/// Reads one bounded immutable metadata record without repair, chmod, staging cleanup or fsync.
fn read(path: &Path, maximum: u64) -> Result<Vec<u8>, String> {
    let file = open_regular(path)?;
    if file.metadata().map_err(|error| error.to_string())?.len() > maximum {
        return Err("Archive original record exceeds bound".into());
    }
    let mut bytes = Vec::new();
    file.take(maximum + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() as u64 > maximum {
        return Err("Archive original record grew beyond bound".into());
    }
    Ok(bytes)
}

/// Publishes an original create-new immutable record with pending-before-final durability.
fn publish(path: &Path, bytes: &[u8]) -> Result<(), String> {
    if bytes.len() as u64 > MAX_METADATA {
        return Err("Archive metadata exceeds bound".into());
    }
    let pending = path.with_extension("pending");
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&pending).map_err(|error| error.to_string())?;
    file.write_all(bytes)
        .and_then(|()| file.sync_all())
        .map_err(|error| error.to_string())?;
    fs::hard_link(&pending, path).map_err(|error| error.to_string())?;
    File::open(path.parent().ok_or("Archive parent unavailable")?)
        .and_then(|file| file.sync_all())
        .map_err(|error| error.to_string())?;
    fs::remove_file(&pending).map_err(|error| error.to_string())?;
    File::open(path.parent().ok_or("Archive parent unavailable")?)
        .and_then(|file| file.sync_all())
        .map_err(|error| error.to_string())?;
    Ok(())
}

impl ContentArchiveSink {
    /// Shares only the already-owned actual CAS and current transport witness; no effects occur here.
    pub(crate) fn new(
        source: FileSystemArtifactStore,
        authorize: Arc<dyn Fn() -> Result<(), String> + Send + Sync>,
    ) -> Self {
        Self {
            source,
            authorize,
            lease: Mutex::new(None),
        }
    }

    /// Derives an original-command path without accepting a request-selected filesystem component.
    fn directory(&self, intent: &ArchiveIntent) -> Result<PathBuf, String> {
        if self.source.owner_id() != Some(intent.request.source_owner.as_str()) {
            return Err("Archive original source owner differs".into());
        }
        if self.source.storage_schema() != artifact_store::ARTIFACT_ARCHIVE_LAYOUT_SCHEMA {
            return Err("original Archive source requires explicit Artifact storage v3".into());
        }
        let root = Path::new(&intent.request.destination);
        if root.starts_with(self.source.root()) || self.source.root().starts_with(root) {
            return Err("Archive source and destination managed roots overlap".into());
        }
        Ok(root
            .join("archives-v1")
            .join(format!(
                "{:x}",
                Sha256::digest(intent.request.authority_id.as_bytes())
            ))
            .join(format!(
                "archive-{:x}",
                Sha256::digest(intent.request.command_id.as_bytes())
            )))
    }

    /// Acquires the original Archive root lease while retaining the original copy-effect budget.
    fn lock_archive(&self, intent: &ArchiveIntent, write: bool) -> Result<(), String> {
        self.acquire_archive_lock(intent, write, write, &|| self.permit(intent))
    }

    /// Separates actual exclusive fencing from permission to create missing maintenance metadata.
    fn acquire_archive_lock(
        &self,
        intent: &ArchiveIntent,
        write: bool,
        create: bool,
        permit: &dyn Fn() -> Result<(), String>,
    ) -> Result<(), String> {
        let mut held = self
            .lease
            .lock()
            .map_err(|_| "Archive original root lease is poisoned")?;
        if let Some((_, exclusive)) = &*held {
            if write && !exclusive {
                return Err(
                    "an original shared observation cannot claim an exclusive copier fence".into(),
                );
            }
            return Ok(());
        }
        let root = Path::new(&intent.request.destination);
        let path = root.join(".archive-writer.lock");
        let file = if write {
            permit()?;
            if create {
                create_directory(root)?;
            } else {
                real_directory(root)?;
            }
            if let Ok(metadata) = fs::symlink_metadata(&path)
                && !metadata.file_type().is_file()
            {
                return Err("Archive writer lease must be an actual regular file".into());
            }
            let mut options = OpenOptions::new();
            options
                .create(create)
                .truncate(false)
                .read(true)
                .write(true);
            #[cfg(target_os = "linux")]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.custom_flags(0x20000).mode(0o600);
            }
            let file = options.open(path).map_err(|error| error.to_string())?;
            file.try_lock().map_err(|error| error.to_string())?;
            if create {
                file.sync_all().map_err(|error| error.to_string())?;
                File::open(root)
                    .and_then(|file| file.sync_all())
                    .map_err(|error| error.to_string())?;
            }
            file
        } else {
            let file = open_regular(&path)?;
            file.try_lock_shared().map_err(|error| error.to_string())?;
            file
        };
        *held = Some((file, write));
        Ok(())
    }

    /// Retains original kernel budget and current grant before each bounded publication effect.
    fn permit(&self, intent: &ArchiveIntent) -> Result<(), String> {
        intent.require_budget()?;
        (self.authorize)()
    }

    /// Copies exact opaque bytes under original admission, checking current authority between chunks.
    fn copy(
        &self,
        target: &FileSystemArtifactStore,
        input: &state::ArchiveContent,
        intent: &ArchiveIntent,
    ) -> Result<(), String> {
        self.permit(intent)?;
        if input.byte_size > 4 * 1024 * 1024 * 1024 {
            return Err("Archive original input exceeds Artifact bound".into());
        }
        let source = self
            .source
            .open_artifact(&input.content_digest)
            .map_err(|error| error.to_string())?;
        if source.metadata().map_err(|error| error.to_string())?.len() != input.byte_size {
            return Err("Archive original input length changed".into());
        }
        let mut source = source.take(input.byte_size + 1);
        let mut upload = target
            .begin_upload(input.content_digest.trim_start_matches("sha256:"))
            .map_err(|error| error.to_string())?;
        let mut buffer = vec![0; CHUNK_BYTES];
        loop {
            self.permit(intent)?;
            let count = source
                .read(&mut buffer)
                .map_err(|error| error.to_string())?;
            if count == 0 {
                break;
            }
            upload
                .write_chunk(&buffer[..count])
                .map_err(|error| error.to_string())?;
        }
        self.permit(intent)?;
        upload
            .finalize(&input.content_digest, input.byte_size)
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    /// Verifies actual published files and original target CAS under its existing shared writer fence.
    fn inspect(&self, intent: &ArchiveIntent) -> Result<String, String> {
        let started = std::time::Instant::now();
        (self.authorize)()?;
        self.lock_archive(intent, false)?;
        let directory = self.directory(intent)?;
        let bytes = read(&directory.join("publication.json"), MAX_METADATA)?;
        let publication: Publication =
            serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
        if publication.schema != SCHEMA
            || &publication.intent != intent
            || read(&directory.join("intent.json"), MAX_METADATA)? != canonical(intent)?
        {
            return Err("Archive original publication identity changed".into());
        }
        if digest(&read(
            &directory.join("events.jsonl"),
            state::MAX_ARCHIVE_BYTES as u64,
        )?) != intent.manifest.segment_digest
            || digest(&read(&directory.join("catalog.json"), MAX_METADATA)?)
                != intent.catalog_digest
            || digest(&read(&directory.join("checkpoint.json"), MAX_METADATA)?)
                != publication.checkpoint_digest
            || publication.checkpoint_digest != digest(&canonical(&intent.checkpoint)?)
            || intent.manifest.checkpoint_digest.as_deref()
                != Some(state::checkpoint_identity(&intent.checkpoint).as_str())
        {
            return Err("Archive original events/Catalog/checkpoint bytes changed".into());
        }
        if read(&directory.join("target-owner.json"), 4096)?
            != canonical(&publication.target_owner)?
        {
            return Err("Archive original target owner receipt changed".into());
        }
        let target = ArtifactReadOnlyStore::open(&directory.join("content"))
            .map_err(|error| error.to_string())?;
        if target.owner_id() != publication.target_owner {
            return Err("Archive actual target owner changed".into());
        }
        let recipient = directory
            .join("content/maintenance-v1/operations")
            .join(format!("{:x}", Sha256::digest(canonical(intent)?)))
            .join("archive-reference.json");
        let expected = RecipientReference {
            schema: "roboguide.archive-recipient-reference/v0.1".into(),
            intent: intent.clone(),
            target_owner: publication.target_owner.clone(),
        };
        if read(&recipient, MAX_METADATA)? != canonical(&expected)? {
            return Err(
                "Archive independent receiving content reference is missing or changed".into(),
            );
        }
        for input in &intent.inputs {
            if started.elapsed().as_secs() >= 30 {
                return Err(
                    "Archive original observation exceeded its independent read budget".into(),
                );
            }
            (self.authorize)()?;
            let file = target
                .open_artifact(&input.content_digest)
                .map_err(|error| error.to_string())?;
            if file.metadata().map_err(|error| error.to_string())?.len() != input.byte_size {
                return Err("Archive retained original byte length changed".into());
            }
            let mut file = file.take(input.byte_size + 1);
            let mut hash = Sha256::new();
            let mut buffer = vec![0; CHUNK_BYTES];
            loop {
                if started.elapsed().as_secs() >= 30 {
                    return Err(
                        "Archive original observation exceeded its independent read budget".into(),
                    );
                }
                (self.authorize)()?;
                let count = file.read(&mut buffer).map_err(|error| error.to_string())?;
                if count == 0 {
                    break;
                }
                hash.update(&buffer[..count]);
            }
            if format!("sha256:{:x}", hash.finalize()) != input.content_digest {
                return Err("Archive retained original bytes are corrupt".into());
            }
        }
        (self.authorize)()?;
        Ok(digest(&canonical(&publication)?))
    }

    /// Re-syncs only exact existing original publication files and directories before source retirement.
    fn confirm(&self, intent: &ArchiveIntent) -> Result<String, String> {
        let proof = self.inspect(intent)?;
        let started = std::time::Instant::now();
        let directory = self.directory(intent)?;
        let target = ArtifactReadOnlyStore::open(&directory.join("content"))
            .map_err(|error| error.to_string())?;
        let mut files = vec![
            "intent.json".to_string(),
            "target-owner.json".into(),
            "events.jsonl".into(),
            "catalog.json".into(),
            "checkpoint.json".into(),
            "publication.json".into(),
            "content/.retention-owner.json".into(),
            "content/blobs".into(),
            "content/maintenance-v1/identity.json".into(),
            "content/reference-authorities-v1/identity.json".into(),
            "content/reference-authorities-v1/inventory.json".into(),
            format!(
                "content/maintenance-v1/operations/{:x}/archive-reference.json",
                Sha256::digest(canonical(intent)?)
            ),
        ];
        for input in &intent.inputs {
            let hex = &input.content_digest[7..];
            files.push(format!("content/objects-v1/sha256/{}/{hex}", &hex[..2]));
        }
        let mut directories = std::collections::BTreeSet::new();
        for name in files {
            (self.authorize)()?;
            if started.elapsed().as_secs() >= 30 {
                return Err(
                    "Archive original durability confirmation exceeded its read budget".into(),
                );
            }
            let path = directory.join(name);
            open_regular(&path)?
                .sync_all()
                .map_err(|error| error.to_string())?;
            for parent in path.ancestors().skip(1) {
                if !parent.starts_with(Path::new(&intent.request.destination)) {
                    break;
                }
                real_directory(parent)?;
                directories.insert(parent.to_path_buf());
            }
        }
        for parent in directories.iter().rev() {
            (self.authorize)()?;
            File::open(parent)
                .and_then(|file| file.sync_all())
                .map_err(|error| error.to_string())?;
        }
        drop(target);
        Ok(proof)
    }
}

impl ArchiveSink for ContentArchiveSink {
    /// The original writer remains held while the actual current TLS/grant witness is rechecked.
    fn require_commit_permission(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        (self.authorize)().map_err(Into::into)
    }
    /// The actual content adapter never downgrades an original command to legacy event-only copying.
    fn append_segment(
        &self,
        _manifest: &ArchiveManifest,
        _bytes: &[u8],
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        Err("Archive content requires its complete original command".into())
    }

    /// Publishes original intent/target identity before source reads, then verifies complete actual bytes.
    fn append_catalog_segment(
        &self,
        manifest: &ArchiveManifest,
        bytes: &[u8],
        catalog: &[PersistedEvent],
        checkpoint: &PersistedCheckpoint,
        intent: &ArchiveIntent,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.permit(intent)?;
        if manifest != &intent.manifest
            || checkpoint != &intent.checkpoint
            || digest(bytes) != manifest.segment_digest
            || digest(&canonical(catalog)?) != intent.catalog_digest
        {
            return Err("Archive source differs from its first durable admission".into());
        }
        let directory = self.directory(intent)?;
        self.lock_archive(intent, true)?;
        self.source
            .bind_reference_authority(&intent.request.authority_id)?;
        create_directory(directory.parent().ok_or("Archive root unavailable")?)?;
        fs::create_dir(&directory)?;
        File::open(directory.parent().ok_or("Archive parent unavailable")?)?.sync_all()?;
        publish(&directory.join("intent.json"), &canonical(intent)?)?;
        self.permit(intent)?;
        let target =
            FileSystemArtifactStore::new_with_archive_references(directory.join("content"))?;
        let target_owner = target
            .owner_id()
            .ok_or("Archive target original identity unavailable")?
            .to_owned();
        publish(
            &directory.join("target-owner.json"),
            &canonical(&target_owner)?,
        )?;
        let recipient = directory
            .join("content/maintenance-v1/operations")
            .join(format!("{:x}", Sha256::digest(canonical(intent)?)));
        create_directory(&recipient)?;
        publish(
            &recipient.join("archive-reference.json"),
            &canonical(&RecipientReference {
                schema: "roboguide.archive-recipient-reference/v0.1".into(),
                intent: intent.clone(),
                target_owner: target_owner.clone(),
            })?,
        )?;
        for input in &intent.inputs {
            self.copy(&target, input, intent)?;
        }
        for (name, data) in [
            ("events.jsonl", bytes.to_vec()),
            ("catalog.json", canonical(catalog)?),
            ("checkpoint.json", canonical(checkpoint)?),
        ] {
            self.permit(intent)?;
            publish(&directory.join(name), &data)?;
        }
        self.permit(intent)?;
        publish(
            &directory.join("publication.json"),
            &canonical(&Publication {
                schema: SCHEMA.into(),
                intent: intent.clone(),
                target_owner,
                checkpoint_digest: digest(&canonical(checkpoint)?),
            })?,
        )?;
        Ok(())
    }

    /// Only observes a complete original publication; it never copies, initializes or renews a budget.
    fn inspect_catalog_segment(
        &self,
        intent: &ArchiveIntent,
    ) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
        self.inspect(intent).map_err(Into::into)
    }

    /// Explicit maintenance confirmation can sync original existing bytes; it never copies new content.
    fn confirm_catalog_segment(
        &self,
        intent: &ArchiveIntent,
    ) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
        self.confirm(intent).map_err(Into::into)
    }
}

mod abort;
