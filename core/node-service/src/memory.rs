//! Node-side Memory ledger and filesystem reference backend.
//!
//! A real Local EAIOS remains the semantic and storage authority reached through configured
//! workflows. This module records immutable manifests for Node-side idempotency and discovery; its
//! filesystem implementation also acts as the fallback reference backend when no workflow exists.

use crate::CompiledMemoryProvider;
use domain::{
    MemoryArtifactManifest, MemoryKind, MemoryOwner, MemoryScope, MemorySelector, MemoryVisibility,
};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// A deterministic metadata query evaluated by a provider workflow or Node-side ledger index.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize)]
pub struct MemoryQuery {
    /// Optional exact logical item and immutable revision.
    pub selector: Option<MemorySelector>,
    /// Optional Memory kind filter.
    pub kind: Option<MemoryKind>,
    /// Optional scope filter.
    pub scope: Option<MemoryScope>,
    /// Optional provider identity filter.
    pub provider_id: Option<String>,
    /// Optional exact payload schema filter.
    pub payload_schema: Option<String>,
    /// Optional exact structured semantic owner filter.
    pub owner: Option<MemoryOwner>,
}

impl MemoryQuery {
    /// Applies the transport-neutral deterministic metadata filter to one manifest.
    pub fn matches(&self, manifest: &MemoryArtifactManifest) -> bool {
        !self
            .selector
            .as_ref()
            .is_some_and(|selector| manifest.selector() != selector)
            && !self.kind.is_some_and(|kind| manifest.kind() != kind)
            && !self
                .scope
                .as_ref()
                .is_some_and(|scope| manifest.scope() != scope)
            && !self
                .provider_id
                .as_deref()
                .is_some_and(|id| manifest.provider_id() != id)
            && !self
                .payload_schema
                .as_deref()
                .is_some_and(|schema| manifest.payload_schema() != schema)
            && !self
                .owner
                .as_ref()
                .is_some_and(|owner| manifest.owner() != owner)
    }
}

/// Node-side ledger failure that never changes Runtime or Task lifecycle by itself.
#[derive(Debug, thiserror::Error)]
pub enum MemoryLedgerError {
    /// Node-side ledger storage could not be read or written.
    #[error("memory ledger I/O failed: {0}")]
    Io(#[from] std::io::Error),
    /// A persisted manifest was malformed.
    #[error("memory ledger manifest JSON failed: {0}")]
    Json(#[from] serde_json::Error),
    /// The recorded manifest violates its configured provider declaration.
    #[error("memory ledger admission failed: {0}")]
    Domain(#[from] domain::DomainError),
    /// The ledger or reference-backend configuration is incomplete.
    #[error("memory ledger configuration failed: {0}")]
    Configuration(String),
}

/// Durable Node-side manifest ledger beside one configured Local Memory Provider.
///
/// This abstraction is not the Local EAIOS semantic or storage authority. Provider operations run
/// through startup-fixed workflows; the ledger records their accepted results and supplies a
/// deterministic reference-backend fallback when an operation has no workflow.
pub trait LocalMemoryLedger: Send + Sync {
    /// Discovers previously recorded metadata without consulting a central index.
    fn discover_recorded(
        &self,
        query: &MemoryQuery,
    ) -> Result<Vec<MemoryArtifactManifest>, MemoryLedgerError>;
    /// Records one immutable manifest after a provider export succeeds.
    fn record_export(&self, manifest: &MemoryArtifactManifest) -> Result<(), MemoryLedgerError>;
    /// Records an import and optionally retains bytes for the workflow-free reference backend.
    fn record_import(
        &self,
        manifest: &MemoryArtifactManifest,
        reference_artifact: Option<&Path>,
    ) -> Result<(), MemoryLedgerError>;
}

/// Filesystem manifest ledger and workflow-free reference backend.
#[derive(Debug, Clone)]
pub struct FilesystemMemoryLedger {
    /// Startup-validated provider declaration used for ledger admission.
    descriptor: CompiledMemoryProvider,
    /// Node-owned ledger and optional reference-backend root.
    root: PathBuf,
    /// Serializes JSONL reads and appends within this process.
    access: Arc<Mutex<()>>,
}

impl FilesystemMemoryLedger {
    /// Opens a Node-side ledger directory, creating it when necessary.
    pub fn open(descriptor: CompiledMemoryProvider) -> Result<Self, MemoryLedgerError> {
        let root = descriptor.storage_directory().to_path_buf();
        fs::create_dir_all(&root)?;
        let ledger = Self {
            descriptor,
            root,
            access: Arc::new(Mutex::new(())),
        };
        ledger.rebuild_index()?;
        Ok(ledger)
    }

    /// Returns the provider identity.
    pub fn provider_id(&self) -> &str {
        self.descriptor.id()
    }

    /// Returns the rebuildable Node-side JSONL index path.
    fn manifest_log(&self) -> PathBuf {
        self.root.join("manifests.jsonl")
    }

    /// Returns the directory containing immutable ledger manifest objects.
    fn manifest_directory(&self) -> PathBuf {
        self.root.join("manifests")
    }

    /// Returns a filesystem-safe deterministic object path for one logical immutable selector.
    fn manifest_object_path(&self, selector: &MemorySelector) -> PathBuf {
        let mut hasher = Sha256::new();
        hasher.update(selector.to_string().as_bytes());
        self.manifest_directory()
            .join(format!("{:x}.json", hasher.finalize()))
    }

    /// Validates and idempotently records one provider-qualified immutable manifest.
    fn append(&self, manifest: &MemoryArtifactManifest) -> Result<(), MemoryLedgerError> {
        self.descriptor_admit(manifest)?;
        let _guard = self.access.lock().map_err(|_| {
            MemoryLedgerError::Configuration("memory ledger lock is poisoned".to_string())
        })?;
        if let Some(existing) = self
            .load_index()?
            .into_iter()
            .find(|existing| existing.selector() == manifest.selector())
        {
            return if existing == *manifest {
                Ok(())
            } else {
                Err(MemoryLedgerError::Configuration(format!(
                    "selector {} already names different immutable Memory",
                    manifest.selector()
                )))
            };
        }
        self.append_unchecked(manifest)
    }

    /// Persists a ledger manifest object and rebuilds its derived JSONL retrieval index.
    fn append_unchecked(&self, manifest: &MemoryArtifactManifest) -> Result<(), MemoryLedgerError> {
        self.persist_manifest_object(manifest)?;
        self.rebuild_index()
    }

    /// Reads the derived Node-side JSONL retrieval index.
    fn read_index(&self) -> Result<Vec<MemoryArtifactManifest>, MemoryLedgerError> {
        let path = self.manifest_log();
        let file = fs::File::open(path)?;
        let manifests = BufReader::new(file)
            .lines()
            .filter_map(|line| match line {
                Ok(line) if line.trim().is_empty() => None,
                value => Some(value),
            })
            .map(|line| {
                let line = line?;
                let manifest: MemoryArtifactManifest = serde_json::from_str(&line)?;
                manifest.validate()?;
                Ok(manifest)
            })
            .collect::<Result<Vec<_>, MemoryLedgerError>>()?;
        normalize_manifests(manifests, "memory ledger index")
    }

    /// Loads a valid index, rebuilding it when deleted or structurally corrupted.
    fn load_index(&self) -> Result<Vec<MemoryArtifactManifest>, MemoryLedgerError> {
        if !self.manifest_log().exists() {
            self.rebuild_index()?;
        }
        match self.read_index() {
            Ok(manifests) => Ok(manifests),
            Err(
                MemoryLedgerError::Json(_)
                | MemoryLedgerError::Domain(_)
                | MemoryLedgerError::Configuration(_),
            ) => {
                self.rebuild_index()?;
                self.read_index()
            }
            Err(error) => Err(error),
        }
    }

    /// Writes one immutable ledger manifest object before changing the derived index.
    fn persist_manifest_object(
        &self,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), MemoryLedgerError> {
        let directory = self.manifest_directory();
        fs::create_dir_all(&directory)?;
        let path = self.manifest_object_path(manifest.selector());
        if path.exists() {
            let existing: MemoryArtifactManifest = serde_json::from_reader(fs::File::open(path)?)?;
            return if existing == *manifest {
                Ok(())
            } else {
                Err(MemoryLedgerError::Configuration(format!(
                    "selector {} already names a different ledger manifest object",
                    manifest.selector()
                )))
            };
        }
        let temporary = path.with_extension(format!("json.tmp-{}", std::process::id()));
        let _ = fs::remove_file(&temporary);
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        let result = (|| -> Result<(), MemoryLedgerError> {
            serde_json::to_writer(&mut file, manifest)?;
            file.write_all(b"\n")?;
            file.sync_data()?;
            fs::rename(&temporary, &path)?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result
    }

    /// Reads durable ledger manifest objects in deterministic filename order.
    fn read_manifest_objects(&self) -> Result<Vec<MemoryArtifactManifest>, MemoryLedgerError> {
        let directory = self.manifest_directory();
        fs::create_dir_all(&directory)?;
        let mut paths = fs::read_dir(directory)?
            .map(|entry| entry.map(|entry| entry.path()))
            .collect::<Result<Vec<_>, _>>()?;
        paths.retain(|path| {
            path.extension()
                .is_some_and(|extension| extension == "json")
        });
        paths.sort();
        let manifests = paths
            .into_iter()
            .map(|path| {
                let manifest: MemoryArtifactManifest =
                    serde_json::from_reader(fs::File::open(&path)?)?;
                manifest.validate()?;
                if path != self.manifest_object_path(manifest.selector()) {
                    return Err(MemoryLedgerError::Configuration(
                        "ledger manifest object filename does not match its selector".to_string(),
                    ));
                }
                Ok(manifest)
            })
            .collect::<Result<Vec<MemoryArtifactManifest>, _>>()?;
        normalize_manifests(manifests, "memory ledger manifest objects")
    }

    /// Rebuilds the JSONL index solely from durable ledger manifest objects.
    fn rebuild_index(&self) -> Result<(), MemoryLedgerError> {
        let manifests = self.read_manifest_objects()?;
        let path = self.manifest_log();
        let temporary = path.with_extension(format!("jsonl.tmp-{}", std::process::id()));
        let _ = fs::remove_file(&temporary);
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&temporary)?;
        let result = (|| -> Result<(), MemoryLedgerError> {
            for manifest in manifests {
                serde_json::to_writer(&mut file, &manifest)?;
                file.write_all(b"\n")?;
            }
            file.sync_data()?;
            fs::rename(&temporary, &path)?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result
    }

    /// Checks an exported manifest against this provider's owner and maximum policy.
    fn descriptor_admit(&self, manifest: &MemoryArtifactManifest) -> Result<(), MemoryLedgerError> {
        let local_system = domain::LocalSystemId::new(self.descriptor.owner().to_string())?;
        let provider = domain::MemoryProviderDescriptor::new(
            self.descriptor.id(),
            local_system,
            parse_kind(self.descriptor.kind())?,
            parse_scope(self.descriptor.scope())?,
            parse_visibility(self.descriptor.visibility())?,
            self.descriptor.payload_schema(),
            self.descriptor.media_type(),
        )?;
        provider.admit_manifest(manifest)?;
        Ok(())
    }

    /// Checks incoming semantics without requiring the remote producer to equal this provider.
    fn descriptor_admit_import(
        &self,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), MemoryLedgerError> {
        manifest.validate()?;
        if manifest.kind() != parse_kind(self.descriptor.kind())?
            || manifest.payload_schema() != self.descriptor.payload_schema()
            || manifest.media_type() != self.descriptor.media_type()
            || manifest.visibility() != MemoryVisibility::Exchangeable
        {
            return Err(MemoryLedgerError::Configuration(
                "imported Memory does not match provider kind/schema/media/visibility".to_string(),
            ));
        }
        let scope_allowed = match self.descriptor.scope() {
            "local" => matches!(manifest.scope(), MemoryScope::Local),
            "global" => true,
            _ => false,
        };
        if !scope_allowed {
            return Err(MemoryLedgerError::Configuration(
                "imported Memory exceeds provider maximum scope".to_string(),
            ));
        }
        Ok(())
    }
}

impl LocalMemoryLedger for FilesystemMemoryLedger {
    /// Reads the rebuildable JSONL index and applies deterministic metadata filters.
    fn discover_recorded(
        &self,
        query: &MemoryQuery,
    ) -> Result<Vec<MemoryArtifactManifest>, MemoryLedgerError> {
        let _guard = self.access.lock().map_err(|_| {
            MemoryLedgerError::Configuration("memory ledger lock is poisoned".to_string())
        })?;
        let mut manifests = Vec::new();
        for manifest in self.load_index()? {
            if !query.matches(&manifest) {
                continue;
            }
            manifests.push(manifest);
        }
        normalize_manifests(manifests, "memory ledger index")
    }

    /// Records one immutable manifest after provider export success.
    fn record_export(&self, manifest: &MemoryArtifactManifest) -> Result<(), MemoryLedgerError> {
        self.append(manifest)
    }

    /// Records imported metadata and retains bytes only for the reference-backend fallback.
    fn record_import(
        &self,
        manifest: &MemoryArtifactManifest,
        reference_artifact: Option<&Path>,
    ) -> Result<(), MemoryLedgerError> {
        self.descriptor_admit_import(manifest)?;
        match (self.descriptor.import().is_some(), reference_artifact) {
            (true, Some(_)) => {
                return Err(MemoryLedgerError::Configuration(
                    "workflow-backed import must not copy payload bytes into the Node ledger"
                        .to_string(),
                ));
            }
            (false, None) => {
                return Err(MemoryLedgerError::Configuration(
                    "workflow-free reference import requires staged artifact bytes".to_string(),
                ));
            }
            (true, None) | (false, Some(_)) => {}
        }
        let _guard = self.access.lock().map_err(|_| {
            MemoryLedgerError::Configuration("memory ledger lock is poisoned".to_string())
        })?;
        if reference_artifact.is_some_and(|artifact| !artifact.is_file()) {
            return Err(MemoryLedgerError::Configuration(
                "reference-backend artifact is not a regular file".to_string(),
            ));
        }
        if let Some(existing) = self
            .load_index()?
            .into_iter()
            .find(|existing| existing.selector() == manifest.selector())
        {
            return if existing == *manifest {
                Ok(())
            } else {
                Err(MemoryLedgerError::Configuration(format!(
                    "selector {} already names different imported Memory",
                    manifest.selector()
                )))
            };
        }
        if let (Some(reference_artifact), Some(artifact)) =
            (reference_artifact, manifest.artifact())
        {
            let destination = self.root.join(format!(
                "{}.blob",
                artifact.content_digest().as_str().replace(':', "_")
            ));
            fs::copy(reference_artifact, destination)?;
        }
        self.append_unchecked(manifest)
    }
}

/// Parses the closed provider kind spelling accepted by node configuration.
fn parse_kind(value: &str) -> Result<MemoryKind, MemoryLedgerError> {
    serde_json::from_value(Value::String(value.to_string()))
        .map_err(|_| MemoryLedgerError::Configuration(format!("unsupported memory kind `{value}`")))
}

/// Parses the static provider maximum scope spelling.
fn parse_scope(value: &str) -> Result<domain::MemoryScopeLimit, MemoryLedgerError> {
    match value {
        "local" => Ok(domain::MemoryScopeLimit::Local),
        "global" => Ok(domain::MemoryScopeLimit::Global),
        _ => Err(MemoryLedgerError::Configuration(format!(
            "unsupported memory scope `{value}`"
        ))),
    }
}

/// Parses the provider maximum visibility spelling.
fn parse_visibility(value: &str) -> Result<MemoryVisibility, MemoryLedgerError> {
    match value {
        "discoverable" => Ok(MemoryVisibility::Discoverable),
        "exchangeable" => Ok(MemoryVisibility::Exchangeable),
        _ => Err(MemoryLedgerError::Configuration(format!(
            "unsupported memory visibility `{value}`"
        ))),
    }
}

/// Sorts manifest metadata while rejecting one selector with conflicting immutable content.
fn normalize_manifests(
    mut manifests: Vec<MemoryArtifactManifest>,
    source: &str,
) -> Result<Vec<MemoryArtifactManifest>, MemoryLedgerError> {
    manifests.sort_by(|left, right| left.selector().cmp(right.selector()));
    if manifests
        .windows(2)
        .any(|pair| pair[0].selector() == pair[1].selector() && pair[0] != pair[1])
    {
        return Err(MemoryLedgerError::Configuration(format!(
            "{source} contains conflicting immutable selectors"
        )));
    }
    manifests.dedup();
    Ok(manifests)
}
