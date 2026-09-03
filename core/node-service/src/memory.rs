//! Heterogeneous Local Memory Provider boundary and the filesystem reference provider.
//!
//! A provider owns semantic Memory metadata and its local representation.  Artifact bytes remain
//! opaque content-addressed data handled by the independent Artifact data plane; this module only
//! records manifests and local replica evidence.

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

/// A deterministic metadata query evaluated by a provider-local index.
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

/// Provider failure that never changes Runtime or Task lifecycle by itself.
#[derive(Debug, thiserror::Error)]
pub enum MemoryProviderError {
    /// Local provider storage could not be read or written.
    #[error("memory provider I/O failed: {0}")]
    Io(#[from] std::io::Error),
    /// A persisted manifest was malformed.
    #[error("memory provider manifest JSON failed: {0}")]
    Json(#[from] serde_json::Error),
    /// The manifest violates its provider declaration.
    #[error("memory provider admission failed: {0}")]
    Domain(#[from] domain::DomainError),
    /// The provider configuration is incomplete.
    #[error("memory provider configuration failed: {0}")]
    Configuration(String),
}

/// Local semantic Memory authority implemented by one heterogeneous EAIOS integration.
pub trait LocalMemoryProvider: Send + Sync {
    /// Discovers metadata without copying content or consulting a central index.
    fn discover(
        &self,
        query: &MemoryQuery,
    ) -> Result<Vec<MemoryArtifactManifest>, MemoryProviderError>;
    /// Records one immutable locally produced Memory manifest.
    fn export(&self, manifest: &MemoryArtifactManifest) -> Result<(), MemoryProviderError>;
    /// Records one selectively imported manifest and its CAS-verified staged opaque bytes.
    fn import(
        &self,
        manifest: &MemoryArtifactManifest,
        staged_artifact: &Path,
    ) -> Result<(), MemoryProviderError>;
}

/// Filesystem provider storing immutable manifests as JSONL and local index metadata as files.
#[derive(Debug, Clone)]
pub struct FilesystemMemoryProvider {
    /// Startup-validated semantic declaration for this local provider.
    descriptor: CompiledMemoryProvider,
    /// Provider-owned storage and rebuildable index root.
    root: PathBuf,
    /// Serializes JSONL reads and appends within this process.
    access: Arc<Mutex<()>>,
}

impl FilesystemMemoryProvider {
    /// Opens a provider-owned directory, creating it when necessary.
    pub fn open(descriptor: CompiledMemoryProvider) -> Result<Self, MemoryProviderError> {
        let root = descriptor.storage_directory().to_path_buf();
        fs::create_dir_all(&root)?;
        let provider = Self {
            descriptor,
            root,
            access: Arc::new(Mutex::new(())),
        };
        provider.rebuild_index()?;
        Ok(provider)
    }

    /// Returns the provider identity.
    pub fn provider_id(&self) -> &str {
        self.descriptor.id()
    }

    /// Returns the append-only provider-local manifest log path.
    fn manifest_log(&self) -> PathBuf {
        self.root.join("manifests.jsonl")
    }

    /// Returns the provider-owned directory containing immutable semantic manifest objects.
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

    /// Validates and idempotently appends one provider-owned immutable manifest.
    fn append(&self, manifest: &MemoryArtifactManifest) -> Result<(), MemoryProviderError> {
        self.descriptor_admit(manifest)?;
        let _guard = self.access.lock().map_err(|_| {
            MemoryProviderError::Configuration("provider index lock is poisoned".to_string())
        })?;
        if let Some(existing) = self
            .load_index()?
            .into_iter()
            .find(|existing| existing.selector() == manifest.selector())
        {
            return if existing == *manifest {
                Ok(())
            } else {
                Err(MemoryProviderError::Configuration(format!(
                    "selector {} already names different immutable Memory",
                    manifest.selector()
                )))
            };
        }
        self.append_unchecked(manifest)
    }

    /// Persists a semantic manifest object and rebuilds its derived JSONL retrieval index.
    fn append_unchecked(
        &self,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), MemoryProviderError> {
        self.persist_manifest_object(manifest)?;
        self.rebuild_index()
    }

    /// Reads the derived provider-local JSONL retrieval index.
    fn read_index(&self) -> Result<Vec<MemoryArtifactManifest>, MemoryProviderError> {
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
            .collect::<Result<Vec<_>, MemoryProviderError>>()?;
        normalize_manifests(manifests, "provider index")
    }

    /// Loads a valid index, rebuilding it when deleted or structurally corrupted.
    fn load_index(&self) -> Result<Vec<MemoryArtifactManifest>, MemoryProviderError> {
        if !self.manifest_log().exists() {
            self.rebuild_index()?;
        }
        match self.read_index() {
            Ok(manifests) => Ok(manifests),
            Err(
                MemoryProviderError::Json(_)
                | MemoryProviderError::Domain(_)
                | MemoryProviderError::Configuration(_),
            ) => {
                self.rebuild_index()?;
                self.read_index()
            }
            Err(error) => Err(error),
        }
    }

    /// Writes one immutable semantic manifest object before changing the derived index.
    fn persist_manifest_object(
        &self,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), MemoryProviderError> {
        let directory = self.manifest_directory();
        fs::create_dir_all(&directory)?;
        let path = self.manifest_object_path(manifest.selector());
        if path.exists() {
            let existing: MemoryArtifactManifest = serde_json::from_reader(fs::File::open(path)?)?;
            return if existing == *manifest {
                Ok(())
            } else {
                Err(MemoryProviderError::Configuration(format!(
                    "selector {} already names a different semantic manifest object",
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
        let result = (|| -> Result<(), MemoryProviderError> {
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

    /// Reads authoritative semantic manifest objects in deterministic filename order.
    fn read_manifest_objects(&self) -> Result<Vec<MemoryArtifactManifest>, MemoryProviderError> {
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
                    return Err(MemoryProviderError::Configuration(
                        "semantic manifest object filename does not match its selector".to_string(),
                    ));
                }
                Ok(manifest)
            })
            .collect::<Result<Vec<MemoryArtifactManifest>, _>>()?;
        normalize_manifests(manifests, "semantic manifest objects")
    }

    /// Rebuilds the JSONL retrieval index solely from provider-owned semantic manifest objects.
    fn rebuild_index(&self) -> Result<(), MemoryProviderError> {
        let manifests = self.read_manifest_objects()?;
        let path = self.manifest_log();
        let temporary = path.with_extension(format!("jsonl.tmp-{}", std::process::id()));
        let _ = fs::remove_file(&temporary);
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&temporary)?;
        let result = (|| -> Result<(), MemoryProviderError> {
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
    fn descriptor_admit(
        &self,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), MemoryProviderError> {
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
    ) -> Result<(), MemoryProviderError> {
        manifest.validate()?;
        if manifest.kind() != parse_kind(self.descriptor.kind())?
            || manifest.payload_schema() != self.descriptor.payload_schema()
            || manifest.media_type() != self.descriptor.media_type()
            || manifest.visibility() != MemoryVisibility::Exchangeable
        {
            return Err(MemoryProviderError::Configuration(
                "imported Memory does not match provider kind/schema/media/visibility".to_string(),
            ));
        }
        let scope_allowed = match self.descriptor.scope() {
            "local" => matches!(manifest.scope(), MemoryScope::Local),
            "global" => true,
            _ => false,
        };
        if !scope_allowed {
            return Err(MemoryProviderError::Configuration(
                "imported Memory exceeds provider maximum scope".to_string(),
            ));
        }
        Ok(())
    }
}

impl LocalMemoryProvider for FilesystemMemoryProvider {
    /// Reads the rebuildable JSONL index and applies deterministic metadata filters.
    fn discover(
        &self,
        query: &MemoryQuery,
    ) -> Result<Vec<MemoryArtifactManifest>, MemoryProviderError> {
        let _guard = self.access.lock().map_err(|_| {
            MemoryProviderError::Configuration("provider index lock is poisoned".to_string())
        })?;
        let mut manifests = Vec::new();
        for manifest in self.load_index()? {
            if !query.matches(&manifest) {
                continue;
            }
            manifests.push(manifest);
        }
        normalize_manifests(manifests, "provider index")
    }

    /// Appends one immutable manifest to the provider-owned log.
    fn export(&self, manifest: &MemoryArtifactManifest) -> Result<(), MemoryProviderError> {
        self.append(manifest)
    }

    /// Persists imported metadata and copies bytes into provider-local storage.
    fn import(
        &self,
        manifest: &MemoryArtifactManifest,
        staged_artifact: &Path,
    ) -> Result<(), MemoryProviderError> {
        self.descriptor_admit_import(manifest)?;
        let _guard = self.access.lock().map_err(|_| {
            MemoryProviderError::Configuration("provider index lock is poisoned".to_string())
        })?;
        if !staged_artifact.is_file() {
            return Err(MemoryProviderError::Configuration(
                "staged artifact is not a regular file".to_string(),
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
                Err(MemoryProviderError::Configuration(format!(
                    "selector {} already names different imported Memory",
                    manifest.selector()
                )))
            };
        }
        if let Some(artifact) = manifest.artifact() {
            let destination = self.root.join(format!(
                "{}.blob",
                artifact.content_digest().as_str().replace(':', "_")
            ));
            fs::copy(staged_artifact, destination)?;
        }
        self.append_unchecked(manifest)
    }
}

/// Parses the closed provider kind spelling accepted by node configuration.
fn parse_kind(value: &str) -> Result<MemoryKind, MemoryProviderError> {
    serde_json::from_value(Value::String(value.to_string())).map_err(|_| {
        MemoryProviderError::Configuration(format!("unsupported memory kind `{value}`"))
    })
}

/// Parses the static provider maximum scope spelling.
fn parse_scope(value: &str) -> Result<domain::MemoryScopeLimit, MemoryProviderError> {
    match value {
        "local" => Ok(domain::MemoryScopeLimit::Local),
        "global" => Ok(domain::MemoryScopeLimit::Global),
        _ => Err(MemoryProviderError::Configuration(format!(
            "unsupported memory scope `{value}`"
        ))),
    }
}

/// Parses the provider maximum visibility spelling.
fn parse_visibility(value: &str) -> Result<MemoryVisibility, MemoryProviderError> {
    match value {
        "discoverable" => Ok(MemoryVisibility::Discoverable),
        "exchangeable" => Ok(MemoryVisibility::Exchangeable),
        _ => Err(MemoryProviderError::Configuration(format!(
            "unsupported memory visibility `{value}`"
        ))),
    }
}

/// Sorts manifest metadata while rejecting one selector with conflicting immutable content.
fn normalize_manifests(
    mut manifests: Vec<MemoryArtifactManifest>,
    source: &str,
) -> Result<Vec<MemoryArtifactManifest>, MemoryProviderError> {
    manifests.sort_by(|left, right| left.selector().cmp(right.selector()));
    if manifests
        .windows(2)
        .any(|pair| pair[0].selector() == pair[1].selector() && pair[0] != pair[1])
    {
        return Err(MemoryProviderError::Configuration(format!(
            "{source} contains conflicting immutable selectors"
        )));
    }
    manifests.dedup();
    Ok(manifests)
}
