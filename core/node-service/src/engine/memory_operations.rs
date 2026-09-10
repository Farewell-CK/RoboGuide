//! Engine setup and Local EAIOS Memory operations.

use super::*;

impl LocalIntegrationEngine {
    /// Creates an engine and opens its durable SQLite WAL journal.
    pub fn new(
        catalog: CompiledLocalCatalog,
        drivers: impl IntoIterator<Item = Arc<dyn LocalDriver>>,
    ) -> Result<Self, EngineError> {
        std::fs::create_dir_all(catalog.state_directory()).map_err(EngineError::Io)?;
        let artifact_stager = catalog
            .artifact_service()
            .map(ArtifactStager::from_compiled)
            .transpose()
            .map_err(EngineError::Artifact)?;
        let journal_path = catalog.state_directory().join("execution-journal.sqlite3");
        let journal = ExecutionJournal::open(journal_path)?;
        let mut driver_map = BTreeMap::new();
        for driver in drivers {
            if driver_map.insert(driver.kind(), driver).is_some() {
                return Err(EngineError::Configuration(
                    "duplicate local driver implementation".to_string(),
                ));
            }
        }
        let mut memory_ledgers: BTreeMap<String, Arc<dyn LocalMemoryLedger>> = BTreeMap::new();
        for descriptor in catalog
            .memory_providers()
            .values()
            .filter(|provider| provider.operational())
        {
            let ledger = FilesystemMemoryLedger::open(descriptor.clone())
                .map_err(|error| EngineError::Memory(error.to_string()))?;
            memory_ledgers.insert(descriptor.id().to_string(), Arc::new(ledger));
        }
        for connection in catalog.connections().values() {
            if !driver_map.contains_key(&connection.driver_kind()) {
                return Err(EngineError::Configuration(format!(
                    "no driver installed for connection {}",
                    connection.id()
                )));
            }
        }
        let (events, _) = broadcast::channel(256);
        let (peer_readiness, _) = broadcast::channel(256);
        Ok(Self {
            inner: Arc::new(EngineInner {
                catalog: Arc::new(catalog),
                journal: Arc::new(journal),
                drivers: driver_map,
                locks: Mutex::new(BTreeMap::new()),
                cancellations_in_flight: Mutex::new(BTreeSet::new()),
                artifact_finalizations_in_flight: Mutex::new(BTreeSet::new()),
                events,
                peer_readiness,
                artifact_stager,
                memory_ledgers,
            }),
        })
    }

    /// Returns the immutable compiled catalog.
    pub fn catalog(&self) -> &CompiledLocalCatalog {
        &self.inner.catalog
    }

    /// Returns the optional node-owned Spatial Memory stager configured at startup.
    ///
    /// Execution workflows must call this explicit facade for static artifact bindings; the
    /// canonical invocation and Node Protocol remain unaware of local cache paths.
    pub fn artifact_stager(&self) -> Option<&ArtifactStager> {
        self.inner.artifact_stager.as_ref()
    }

    /// Returns the provider-authorized publish-eligible Memory set.
    ///
    /// A configured Local EAIOS workflow is the semantic authority for this set; its response is
    /// not interpreted as all Memory available in the local system. RoboGuide only validates the
    /// immutable manifest shape, applies the explicit query and live-scope safety filter, and
    /// returns the resulting metadata for the publication mechanism. When no workflow exists,
    /// the Node ledger is used only as the documented reference-backend fallback.
    pub async fn discover_memories(
        &self,
        provider_id: &str,
        query: &MemoryQuery,
        mut invocation: serde_json::Value,
    ) -> Result<Vec<MemoryArtifactManifest>, EngineError> {
        validate_memory_query_scope(query, &invocation)?;
        let ledger = self
            .inner
            .memory_ledgers
            .get(provider_id)
            .ok_or_else(|| EngineError::Memory(format!("unknown memory provider `{provider_id}`")))?
            .clone();
        let workflow = self
            .catalog()
            .memory_providers()
            .get(provider_id)
            .and_then(|p| p.discover());
        let manifests = if let Some(workflow) = workflow {
            if let Some(object) = invocation.as_object_mut() {
                object.insert(
                    "memory_query".to_string(),
                    serde_json::to_value(query).map_err(EngineError::Json)?,
                );
            }
            let mut context = WorkflowContext::new(invocation.clone());
            self.run_steps(workflow.steps(), &mut context).await?;
            let pointer = workflow.manifests_pointer().ok_or_else(|| {
                EngineError::Memory("discover workflow requires manifests_pointer".to_string())
            })?;
            let value = context.as_json().pointer(pointer).ok_or_else(|| {
                EngineError::Memory(format!("discover response missing `{pointer}`"))
            })?;
            serde_json::from_value(value.clone()).map_err(EngineError::Json)?
        } else {
            ledger
                .discover_recorded(query)
                .map_err(|error| EngineError::Memory(error.to_string()))?
        };
        accept_provider_discovery(manifests, query, &invocation)
    }

    /// Exports one immutable Memory manifest through local provider authority.
    pub async fn export_memory(
        &self,
        provider_id: &str,
        manifest: &MemoryArtifactManifest,
        invocation: serde_json::Value,
    ) -> Result<Option<PathBuf>, EngineError> {
        validate_memory_operation_scope(manifest, &invocation)?;
        self.validate_memory_export(provider_id, manifest)?;
        let ledger = self
            .inner
            .memory_ledgers
            .get(provider_id)
            .ok_or_else(|| EngineError::Memory(format!("unknown memory provider `{provider_id}`")))?
            .clone();
        let mut artifact_path = None;
        if let Some(workflow) = self
            .catalog()
            .memory_providers()
            .get(provider_id)
            .and_then(|p| p.export())
        {
            let mut value = invocation;
            if let Some(object) = value.as_object_mut() {
                object.insert(
                    "memory_manifest".to_string(),
                    serde_json::to_value(manifest).map_err(EngineError::Json)?,
                );
            }
            let mut context = WorkflowContext::new(value);
            self.run_steps(workflow.steps(), &mut context).await?;
            if let Some(pointer) = workflow.artifact_path_pointer() {
                let relative_path = context
                    .as_json()
                    .pointer(pointer)
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| {
                        EngineError::Memory(format!("export response missing path `{pointer}`"))
                    })?;
                let handoff_root =
                    self.catalog().memory_providers()[provider_id].storage_directory();
                artifact_path = Some(resolve_memory_export_path(handoff_root, relative_path)?);
            }
        }
        ledger
            .record_export(manifest)
            .map_err(|error| EngineError::Memory(error.to_string()))?;
        Ok(artifact_path)
    }

    /// Applies one provider's complete local semantic admission before export side effects.
    pub(crate) fn validate_memory_export(
        &self,
        provider_id: &str,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), EngineError> {
        let provider = self
            .catalog()
            .memory_providers()
            .get(provider_id)
            .ok_or_else(|| {
                EngineError::Memory(format!("unknown memory provider `{provider_id}`"))
            })?;
        manifest
            .validate()
            .map_err(|error| EngineError::Memory(error.to_string()))?;
        let domain::MemoryOwner::Node {
            node_id,
            local_system_id,
        } = manifest.owner()
        else {
            return Err(EngineError::Memory(
                "local provider export requires Node-owned Memory".to_string(),
            ));
        };
        let scope_allowed = match provider.scope() {
            "local" => matches!(manifest.scope(), domain::MemoryScope::Local),
            "global" => true,
            _ => false,
        };
        let visibility_allowed = provider.visibility() == "exchangeable"
            || manifest.visibility() == domain::MemoryVisibility::Discoverable;
        if node_id.as_str() != self.catalog().node_id()
            || local_system_id.as_str() != provider.owner()
            || manifest.provider_id() != provider.id()
            || memory_kind_name(manifest.kind()) != provider.kind()
            || !scope_allowed
            || !visibility_allowed
            || manifest.payload_schema() != provider.payload_schema()
            || manifest.media_type() != provider.media_type()
            || manifest.payload_schema() == domain::SPATIAL_MEMORY_SCHEMA_V0_1
        {
            return Err(EngineError::Memory(
                "Memory export exceeds its local provider declaration".to_string(),
            ));
        }
        Ok(())
    }

    /// Validates remote Memory against the consumer provider before local import side effects.
    pub(super) fn validate_memory_import(
        &self,
        provider_id: &str,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), EngineError> {
        let provider = self
            .catalog()
            .memory_providers()
            .get(provider_id)
            .ok_or_else(|| {
                EngineError::Configuration(format!("unknown memory provider `{provider_id}`"))
            })?;
        manifest
            .validate()
            .map_err(|error| EngineError::Configuration(error.to_string()))?;
        let scope_allowed = match provider.scope() {
            "local" => matches!(manifest.scope(), domain::MemoryScope::Local),
            "global" => true,
            _ => false,
        };
        let local_scope_owner_matches = !matches!(manifest.scope(), domain::MemoryScope::Local)
            || matches!(
                manifest.owner(),
                domain::MemoryOwner::Node { node_id, .. }
                    if node_id.as_str() == self.catalog().node_id()
            );
        if memory_kind_name(manifest.kind()) != provider.kind()
            || manifest.payload_schema() != provider.payload_schema()
            || manifest.media_type() != provider.media_type()
            || provider.visibility() != "exchangeable"
            || manifest.visibility() != domain::MemoryVisibility::Exchangeable
            || !scope_allowed
            || !local_scope_owner_matches
            || manifest.payload_schema() == domain::SPATIAL_MEMORY_SCHEMA_V0_1
        {
            return Err(EngineError::Configuration(
                "Memory import exceeds its local consumer provider declaration".to_string(),
            ));
        }
        Ok(())
    }

    /// Imports through EAIOS authority or the workflow-free filesystem reference backend.
    pub(super) async fn import_memory(
        &self,
        provider_id: &str,
        manifest: &MemoryArtifactManifest,
        staged_artifact: &std::path::Path,
        invocation: serde_json::Value,
    ) -> Result<(), EngineError> {
        validate_memory_operation_scope(manifest, &invocation)?;
        let ledger = self
            .inner
            .memory_ledgers
            .get(provider_id)
            .ok_or_else(|| EngineError::Memory(format!("unknown memory provider `{provider_id}`")))?
            .clone();
        let workflow = self
            .catalog()
            .memory_providers()
            .get(provider_id)
            .and_then(|provider| provider.import());
        if let Some(workflow) = workflow {
            let mut value = invocation;
            if let Some(object) = value.as_object_mut() {
                object.insert(
                    "memory_manifest".to_string(),
                    serde_json::to_value(manifest).map_err(EngineError::Json)?,
                );
                object.insert(
                    "staged_artifact".to_string(),
                    serde_json::Value::String(staged_artifact.to_string_lossy().into_owned()),
                );
            }
            let mut context = WorkflowContext::new(value);
            self.run_steps(workflow.steps(), &mut context).await?;
        }
        let reference_artifact = workflow.is_none().then_some(staged_artifact);
        ledger
            .record_import(manifest, reference_artifact)
            .map_err(|error| EngineError::Memory(error.to_string()))
    }

    /// Selectively stages, imports, and reports one exchangeable Memory revision.
    ///
    /// Replica evidence is observational only. A failure is returned to the caller for retry or
    /// reconciliation and never mutates Runtime, Control, Task, or Mission state.
    pub async fn exchange_memory(
        &self,
        provider_id: &str,
        manifest: &MemoryArtifactManifest,
        session_id: &str,
        invocation: serde_json::Value,
    ) -> Result<(), EngineError> {
        let stager = self.artifact_stager().ok_or_else(|| {
            EngineError::Memory("Memory exchange requires the Artifact data plane".to_string())
        })?;
        let node_id = NodeId::new(self.catalog().node_id().to_string())
            .map_err(|error| EngineError::Memory(error.to_string()))?;
        let ledger = self.inner.memory_ledgers.get(provider_id).ok_or_else(|| {
            EngineError::Memory(format!("unknown memory provider `{provider_id}`"))
        })?;
        let existing = ledger
            .discover_recorded(&MemoryQuery {
                selector: Some(manifest.selector().clone()),
                ..MemoryQuery::default()
            })
            .map_err(|error| EngineError::Memory(error.to_string()))?
            .into_iter()
            .next();
        if existing
            .as_ref()
            .is_some_and(|existing| existing != manifest)
        {
            return Err(EngineError::Configuration(
                "selector already names different immutable Memory in the Node-side ledger"
                    .to_string(),
            ));
        }
        if let Err(error) = validate_memory_operation_scope(manifest, &invocation)
            .and_then(|()| self.validate_memory_import(provider_id, manifest))
        {
            if existing.is_none() {
                let _ = stager
                    .record_memory_replica(
                        manifest,
                        &node_id,
                        session_id,
                        provider_id,
                        "rejected",
                        Some(&error.to_string()),
                    )
                    .await;
            }
            return Err(error);
        }
        if existing.is_some() {
            stager
                .record_memory_replica(
                    manifest,
                    &node_id,
                    session_id,
                    provider_id,
                    "imported",
                    None,
                )
                .await?;
            return Ok(());
        }
        let path = stager.memory_cache_path(manifest.selector());
        if let Err(error) = stager.stage_memory_input(manifest, &path).await {
            let error = EngineError::Artifact(error);
            if artifact_error_is_deterministic(&error) {
                let _ = stager
                    .record_memory_replica(
                        manifest,
                        &node_id,
                        session_id,
                        provider_id,
                        "rejected",
                        Some(&error.to_string()),
                    )
                    .await;
            }
            return Err(error);
        }
        stager
            .record_memory_replica(manifest, &node_id, session_id, provider_id, "staged", None)
            .await?;
        if let Err(error) = self
            .import_memory(provider_id, manifest, &path, invocation)
            .await
        {
            if memory_import_error_is_deterministic(&error) {
                let _ = stager
                    .record_memory_replica(
                        manifest,
                        &node_id,
                        session_id,
                        provider_id,
                        "rejected",
                        Some(&error.to_string()),
                    )
                    .await;
            }
            return Err(error);
        }
        stager
            .record_memory_replica(
                manifest,
                &node_id,
                session_id,
                provider_id,
                "imported",
                None,
            )
            .await?;
        Ok(())
    }
}
