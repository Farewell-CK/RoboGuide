//! Actual parent9 local initialization journal writes; source paths/bytes are opened by owned workers.

use super::*;
use crate::content_store::{ContentBudget, StreamPermission};
use crate::source_initialization::{
    self as original, CapturedInitialization, InitializationAdmission, InitializationCompletion,
    InitializationIntent, InitializationRecord,
};

/// Returns one complete mandatory singleton inventory without interpreting missing authority as empty.
fn heads(sql: &Connection) -> Result<Vec<String>, String> {
    let result = read_meta(sql, original::HEAD_KEY)
        .map_err(|error| error.to_string())?
        .ok_or("original initialization inventory is missing")?;
    Ok(result)
}

/// Writes only bounded retained evidence; original declarations/results never change through retries.
fn write_record(
    sql: &Connection,
    value: &InitializationRecord,
    insert: bool,
) -> Result<(), String> {
    original::validate_record(value)?;
    let raw = serde_json::to_string(value).map_err(|error| error.to_string())?;
    if raw.len() > 256 * 1024 * 1024 {
        return Err("original initialization record exceeds its bound".into());
    }
    let changed = sql
        .execute(
            if insert {
                "INSERT INTO content_initializations(command_id,record) VALUES (?1,?2)"
            } else {
                "UPDATE content_initializations SET record=?2 WHERE command_id=?1"
            },
            params![value.intent.policy.command_id, raw],
        )
        .map_err(|error| error.to_string())?;
    if changed != 1 {
        return Err("original initialization operation disappeared before publication".into());
    }
    Ok(())
}

impl SqliteRaftStore {
    /// Source submission requires the original once-only captured proposal and its still-live first budget.
    pub(crate) fn require_initialization_submission(
        &self,
        candidate: &ContentApplicationBatch,
    ) -> Result<(), String> {
        if candidate.schema != CONTENT_INITIALIZATION_SCHEMA {
            return Ok(());
        }
        let policy = self
            .initialization_policy
            .as_deref()
            .ok_or("original initialization policy is missing")?;
        let sql = self.connection.lock().map_err(|error| error.to_string())?;
        let record = original::record(&sql, &policy.command_id)?
            .ok_or("original initialization intent is missing before submission")?;
        if record.completion.is_some()
            || record
                .captured
                .as_ref()
                .map(|value| value.candidate.as_ref())
                != Some(candidate)
        {
            return Err("source proposal differs from its original unfinished capture".into());
        }
        record.intent.budget.check().map(|_| ())
    }

    /// Checks exact retry inputs before returning original evidence; it never opens source files.
    pub(crate) fn existing_content_initialization(
        &self,
        input: &ContentInitializationInput,
    ) -> Result<Option<ContentInitializationStatus>, String> {
        input.validate()?;
        let observed = self.content_initialization_status()?;
        if observed.is_some() {
            let sql = self.connection.lock().map_err(|error| error.to_string())?;
            let policy = self
                .initialization_policy
                .as_deref()
                .ok_or("original source policy is missing")?;
            let record = original::record(&sql, &policy.command_id)?
                .ok_or("original initialization operation disappeared")?;
            if &record.intent.input != input {
                return Err("original initialization retry changed its first source paths".into());
            }
        }
        Ok(observed)
    }

    /// Reads only retained first evidence and separate local metadata, without source I/O or a new clock.
    pub fn content_initialization_status(
        &self,
    ) -> Result<Option<ContentInitializationStatus>, String> {
        if !self.initialization_content {
            return Err("source initialization status requires parent9".into());
        }
        let Some(policy) = self.initialization_policy.as_deref() else {
            return Ok(None);
        };
        let sql = self.connection.lock().map_err(|error| error.to_string())?;
        let root = self
            .content
            .as_ref()
            .ok_or("original initialization Root is missing")?;
        original::validate_storage(
            &sql,
            Some(root.identity()),
            &self.profile,
            Some(policy),
            true,
        )?;
        original::record(&sql, &policy.command_id)?
            .map(|record| {
                let view = read_view(&sql).map_err(|error| error.to_string())?;
                original::status(&record, &view)
            })
            .transpose()
    }

    /// Atomically retains the actual first declaration before any source metadata/payload opening.
    pub(crate) fn admit_content_initialization(
        &self,
        input: ContentInitializationInput,
        epoch: ControllerEpoch,
        budget: ContentBudget,
        permission: StreamPermission,
    ) -> Result<InitializationAdmission, String> {
        if !self.initialization_content {
            return Err("source initialization requires parent9".into());
        }
        let policy = self
            .initialization_policy
            .as_deref()
            .ok_or("original source declaration is unavailable")?;
        policy.validate()?;
        input.validate()?;
        let root = self
            .content
            .as_ref()
            .ok_or("original initialization Root is missing")?;
        let mut sql = self.connection.lock().map_err(|error| error.to_string())?;
        original::validate_storage(
            &sql,
            Some(root.identity()),
            &self.profile,
            Some(policy),
            true,
        )?;
        if let Some(prior) = original::record(&sql, &policy.command_id)? {
            if prior.intent.input != input || prior.intent.policy != *policy {
                return Err(
                    "original source initialization retry changed its first declaration".into(),
                );
            }
            return original::status(&prior, &read_view(&sql).map_err(|error| error.to_string())?)
                .map(|status| InitializationAdmission::Existing(Box::new(status)));
        }
        if !read_view(&sql)
            .map_err(|error| error.to_string())?
            .is_empty_application()
            || root.identity().member != policy.source_member
            || epoch.replica_id != policy.source_member
            || budget.duration_ms != policy.budget_ms
            || !heads(&sql)?.is_empty()
        {
            return Err(
                "new original initialization has another owner, predecessor or budget".into(),
            );
        }
        let intent = InitializationIntent {
            schema: "roboguide.content-initialization-intent/v0.1".into(),
            profile_identity: self.profile.clone(),
            root: root.identity().clone(),
            policy: policy.clone(),
            input,
            epoch,
            budget,
        };
        let record = InitializationRecord {
            intent_digest: original::identity(&intent)?,
            intent,
            captured: None,
            completion: None,
        };
        permission()?;
        record.intent.budget.check()?;
        let transaction = sql
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        write_record(&transaction, &record, true)?;
        write_meta(
            &transaction,
            original::HEAD_KEY,
            &vec![policy.command_id.clone()],
        )
        .map_err(|error| error.to_string())?;
        permission()?;
        record.intent.budget.check()?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(InitializationAdmission::New(Box::new(record)))
    }

    /// Publishes the original exact capture once, before any transfer or consensus submission.
    pub(crate) fn capture_content_initialization(
        &self,
        mut record: InitializationRecord,
        candidate: ContentApplicationBatch,
        permission: StreamPermission,
    ) -> Result<InitializationRecord, String> {
        self.require_content_initialization(&candidate)?;
        let mut sql = self.connection.lock().map_err(|error| error.to_string())?;
        let prior = original::record(&sql, &record.intent.policy.command_id)?
            .ok_or("original initialization intent is missing")?;
        if prior != record || record.captured.is_some() || record.completion.is_some() {
            return Err("original initialization capture was already fixed or changed".into());
        }
        permission()?;
        record.captured = Some(CapturedInitialization {
            candidate: Box::new(candidate),
            captured_ms: record.intent.budget.check()?,
        });
        let transaction = sql
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        write_record(&transaction, &record, false)?;
        permission()?;
        record.intent.budget.check()?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(record)
    }

    /// Stores only the actual original complete return; later local metadata never fills an old Unknown.
    pub(crate) fn complete_content_initialization(
        &self,
        mut record: InitializationRecord,
        receipt: CommitReceipt,
        complete_members: BTreeSet<u64>,
        permission: StreamPermission,
    ) -> Result<ContentInitializationStatus, String> {
        let mut sql = self.connection.lock().map_err(|error| error.to_string())?;
        let prior = original::record(&sql, &record.intent.policy.command_id)?
            .ok_or("original initialization intent is missing")?;
        if prior != record || record.completion.is_some() {
            return Err("original initialization result was already fixed or changed".into());
        }
        let candidate = record
            .captured
            .as_ref()
            .ok_or("original initialization capture is missing")?;
        let view = read_view(&sql).map_err(|error| error.to_string())?;
        let expected = candidate.candidate.initialization_anchor()?;
        if expected.is_none()
            || view
                .content
                .as_ref()
                .and_then(|content| content.source_initialization.as_ref())
                != expected.as_ref()
        {
            return Err("original initialization result lacks its actual applied lineage".into());
        }
        permission()?;
        record.completion = Some(InitializationCompletion {
            receipt,
            complete_members,
            completed_ms: record.intent.budget.check()?,
        });
        let transaction = sql
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        write_record(&transaction, &record, false)?;
        permission()?;
        record.intent.budget.check()?;
        transaction.commit().map_err(|error| error.to_string())?;
        original::status(&record, &view)
    }
}
