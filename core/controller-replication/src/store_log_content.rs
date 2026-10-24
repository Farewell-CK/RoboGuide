//! Readonly exact retained Log authority for historical content, separate from accepted-view routing.

use super::*;

impl SqliteRaftStore {
    /// Reads only the actual parent row; callbacks must not reacquire a held Root mutex.
    fn retained_log_row(
        &self,
        id: LogId<u64>,
        committed: bool,
    ) -> Result<(ContentApplicationBatch, ReplicaContentProducerSource), String> {
        self.read_network_snapshot(|connection| {
            let raw: Option<String> = connection
                .query_row(
                    "SELECT data FROM replica_v1_log WHERE log_index=?1 AND length(CAST(data AS BLOB))<=?2",
                    params![id.index, MAX_HISTORY_BYTES],
                    |row| row.get(0),
                )
                .optional()
                .map_err(|e| e.to_string())?;
            let raw = raw.ok_or("exact retained historical Log is unavailable or exceeds bounds")?;
            let entry: Entry<ControllerRaftConfig> =
                serde_json::from_str(&raw).map_err(|e| e.to_string())?;
            if entry.log_id != id {
                return Err("historical LogId differs from the actual retained row".into());
            }
            if committed {
                let pointer: Option<LogId<u64>> = read_meta(connection, "committed")
                    .map_err(|e| e.to_string())?;
                if pointer.is_none_or(|pointer| pointer.index < id.index
                    || (pointer.index == id.index && pointer != id)
                    || pointer.leader_id < id.leader_id) {
                    return Err("historical source Log has no durable committed position".into());
                }
            }
            let EntryPayload::Normal(ReplicaCommand::ContentApplication {
                content_application: candidate,
            }) = entry.payload else {
                return Err("historical Log does not contain original content authority".into());
            };
            if candidate.profile_identity != self.profile {
                return Err("historical Log belongs to another original profile".into());
            }
            let source = ReplicaContentProducerSource {
                kind: ReplicaContentProducerKind::Log,
                epoch: ControllerEpoch {
                    term: id.leader_id.term,
                    replica_id: id.leader_id.node_id,
                },
                position: serde_json::to_string(&id).map_err(|e| e.to_string())?,
                authority_digest: candidate.identity()?,
                parent_record_digest: format!("{:x}", Sha256::digest(raw.as_bytes())),
                checkpoint_digest: Some(state::checkpoint_identity(&candidate.batch.checkpoint)),
            };
            Ok((*candidate, source))
        })
    }

    /// Requires the real original Log and active producer; source additionally needs durable Commit.
    pub(crate) fn retained_log_candidate(
        &self,
        id: LogId<u64>,
        committed: bool,
    ) -> Result<ContentApplicationBatch, String> {
        let (candidate, source) = self.retained_log_row(id, committed)?;
        let operation = format!(
            "producer:{:x}",
            Sha256::digest(
                serde_json::to_vec(&serde_json::to_value(&source).map_err(|e| e.to_string())?)
                    .map_err(|e| e.to_string())?
            )
        );
        self.content
            .as_ref()
            .ok_or("historical Log content backend is absent")?
            .require_original_log_producer(&operation, &self._writer, &source, &candidate.inputs)?;
        Ok(candidate)
    }

    /// Checks exact original candidate fields without interpreting them as the current accepted view.
    pub(crate) fn require_historical_log_target(
        &self,
        id: LogId<u64>,
        target: &ContentQuorumTarget,
        committed: bool,
    ) -> Result<(), String> {
        let (candidate, _) = self.retained_log_row(id, committed)?;
        if candidate.identity()? != target.batch_digest
            || candidate.inputs != target.inputs
            || state::checkpoint_identity(&candidate.batch.checkpoint) != target.checkpoint_digest
        {
            return Err("historical target differs from the complete original retained Log".into());
        }
        if committed {
            self.read_network_snapshot(|connection| {
                let revision: u64 = read_meta(connection, "revision")
                    .map_err(|e| e.to_string())?
                    .ok_or("historical source revision is absent")?;
                if revision < target.revision {
                    return Err("historical source regressed below its original observation".into());
                }
                Ok(())
            })?;
        }
        Ok(())
    }

    /// Selects a bounded unapplied committed prefix; the authenticated sender still proves each Log.
    pub(crate) fn pending_content_logs(
        &self,
        through: Option<LogId<u64>>,
    ) -> Result<Vec<LogId<u64>>, String> {
        self.read_network_snapshot(|connection| {
            let applied: Option<LogId<u64>> = read_meta(connection, "applied")
                .map_err(|e| e.to_string())?;
            let mut statement = connection.prepare(
                "SELECT data FROM replica_v1_log WHERE log_index>?1 AND log_index<=?2 ORDER BY log_index LIMIT 16"
            ).map_err(|e| e.to_string())?;
            let rows = statement.query_map(
                params![applied.map_or(0, |id| id.index), through.map_or(0, |id| id.index)],
                |row| row.get::<_, String>(0),
            ).map_err(|e| e.to_string())?;
            let mut result = Vec::new();
            for raw in rows {
                let entry: Entry<ControllerRaftConfig> = serde_json::from_str(
                    &raw.map_err(|e| e.to_string())?
                ).map_err(|e| e.to_string())?;
                if matches!(entry.payload, EntryPayload::Normal(ReplicaCommand::ContentApplication {..})) {
                    result.push(entry.log_id);
                }
            }
            Ok(result)
        })
    }
}
