//! Original committed Log content inspection and explicit existing-Artifact replay repair.

use super::*;

/// One original committed replay requirement whose actual receipt or bytes are unavailable.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UnavailableReplayInput {
    /// Exact original retained Log index; not a newly accepted application command.
    pub log_index: u64,
    /// Hash of the complete actual retained Log row, independently of current metadata revisions.
    pub entry_digest: String,
    /// Exact declared qualified input, preserving its original Artifact owner.
    pub content: ArtifactContentKey,
    /// Actual current verification failure; no original operation result is changed.
    pub unavailable_reason: String,
}

impl SqliteRaftStore {
    /// Verifies deduplicated actual committed-but-unapplied inputs before upstream replay can block.
    pub fn unavailable_replay_inputs(&self) -> Result<Vec<UnavailableReplayInput>, String> {
        let Some(root) = &self.content else {
            return Ok(Vec::new());
        };
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        self.validate_content_producer_heads(&connection)?;
        let Some(committed) =
            read_meta::<LogId<u64>>(&connection, "committed").map_err(|error| error.to_string())?
        else {
            return Ok(Vec::new());
        };
        let applied =
            read_meta::<LogId<u64>>(&connection, "applied").map_err(|error| error.to_string())?;
        let mut statement = connection
            .prepare(
                "SELECT log_index,data FROM replica_v1_log WHERE log_index<=?1 ORDER BY log_index",
            )
            .map_err(|error| error.to_string())?;
        let mut inputs = BTreeMap::new();
        for row in statement
            .query_map([committed.index], |row| {
                Ok((row.get::<_, u64>(0)?, row.get::<_, String>(1)?))
            })
            .map_err(|error| error.to_string())?
        {
            let (index, raw) = row.map_err(|error| error.to_string())?;
            if applied.is_some_and(|applied| index <= applied.index) {
                continue;
            }
            let entry: Entry<ControllerRaftConfig> =
                serde_json::from_str(&raw).map_err(|error| error.to_string())?;
            if entry.log_id.index != index {
                return Err("original replay Log row/index differs".into());
            }
            let EntryPayload::Normal(ReplicaCommand::ContentApplication {
                content_application,
            }) = entry.payload
            else {
                continue;
            };
            self.require_content_initialization(&content_application)?;
            let digest = format!("{:x}", Sha256::digest(raw.as_bytes()));
            for content in content_application.inputs {
                inputs.entry(content).or_insert((index, digest.clone()));
            }
        }
        let mut unavailable = Vec::new();
        for (content, (log_index, entry_digest)) in inputs {
            if let Err(error) = root.verify_content(&content) {
                unavailable.push(UnavailableReplayInput {
                    log_index,
                    entry_digest,
                    content,
                    unavailable_reason: error,
                });
            }
        }
        Ok(unavailable)
    }

    /// Repairs only an exact retained committed input through one original immutable copy operation.
    pub fn repair_committed_input_from_artifact(
        &self,
        log_index: u64,
        entry_digest: &str,
        content: &ArtifactContentKey,
        source_cas: &Path,
        duration_ms: u64,
    ) -> Result<crate::ReplicaContentCopyStatus, String> {
        content.validate()?;
        if !crate::content::hex_digest(entry_digest)
            || !source_cas.is_absolute()
            || source_cas.as_os_str().len() > 4096
            || source_cas
                .components()
                .any(|part| matches!(part, std::path::Component::ParentDir))
        {
            return Err("replay repair requires exact bounded original entry/source input".into());
        }
        let root = self
            .content
            .as_ref()
            .ok_or("replay repair requires original content storage")?;
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        self.validate_content_producer_heads(&connection)?;
        let committed: LogId<u64> = read_meta(&connection, "committed")
            .map_err(|error| error.to_string())?
            .ok_or("replay repair has no original committed Log")?;
        if log_index > committed.index {
            return Err("replay repair cannot borrow an uncommitted input".into());
        }
        let raw: String = connection
            .query_row(
                "SELECT data FROM replica_v1_log WHERE log_index=?1",
                [log_index],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        if format!("{:x}", Sha256::digest(raw.as_bytes())) != entry_digest {
            return Err("replay repair original complete entry digest differs".into());
        }
        let entry: Entry<ControllerRaftConfig> =
            serde_json::from_str(&raw).map_err(|error| error.to_string())?;
        if entry.log_id.index != log_index {
            return Err("replay repair original Log row/index differs".into());
        }
        let EntryPayload::Normal(ReplicaCommand::ContentApplication {
            content_application,
        }) = &entry.payload
        else {
            return Err("replay repair Log has no original content requirement".into());
        };
        self.require_content_initialization(content_application)?;
        if !content_application.inputs.contains(content) {
            return Err("replay repair content is not an original declared Log input".into());
        }
        let original = self
            .parent_producers()
            .map_err(|error| error.to_string())?
            .into_iter()
            .find(|producer| {
                producer.original["intent"]["source"]["kind"] == "log"
                    && producer.original["intent"]["source"]["parent_record_digest"] == entry_digest
            })
            .ok_or("replay repair original Log producer is unavailable")?;
        if !self
            .producer_is_referenced(&connection, &original)
            .map_err(|error| error.to_string())?
            .0
        {
            return Err("replay repair original producer no longer retains this Log".into());
        }
        let id = format!(
            "replay-repair:{:x}",
            Sha256::digest(
                serde_json::to_vec(&(
                    &self.profile,
                    &root.identity().store_owner,
                    entry.log_id,
                    content,
                ))
                .map_err(|error| error.to_string())?
            )
        );
        let authority = format!(
            "{:x}",
            Sha256::digest(
                serde_json::to_vec(&(
                    "roboguide.committed-input-repair/v0.1",
                    entry_digest,
                    source_cas,
                    duration_ms,
                ))
                .map_err(|error| error.to_string())?
            )
        );
        if let Some(original) = root.bound_copy_status(&id, content, &authority, duration_ms)? {
            return Ok(original);
        }
        let source = artifact_store::FileSystemArtifactStore::open_existing_managed(source_cas)
            .map_err(|error| error.to_string())?;
        root.copy_from_artifact(&source, content, &id, &authority, duration_ms)
    }
}
