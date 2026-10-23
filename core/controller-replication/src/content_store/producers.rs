//! Original member-local producer intent/permit/outcome; no caller JSON creates publication authority.

use super::copy::digest;
use super::time::ContentBudget;
use super::*;
use crate::ControllerEpoch;
use std::collections::BTreeSet;

/// Actual process/kernel admission sampled before the real bounded blocking queue; cloning never renews it.
#[derive(Clone)]
pub(crate) struct ProducerClock(ContentBudget);

impl ProducerClock {
    /// Retains an already-issued actual adoption budget without resampling its original clock.
    pub(crate) fn from_original(budget: &ContentBudget) -> Result<Self, String> {
        budget.check()?;
        Ok(Self(budget.clone()))
    }

    /// Returns only the immutable original duration used by actual producer admissions.
    pub(crate) fn duration_ms(&self) -> u64 {
        self.0.duration_ms
    }

    /// Samples one original five-minute work budget before any queue or original parent writer wait.
    pub(crate) fn issue() -> Result<Self, String> {
        ContentBudget::issue(300_000).map(Self)
    }

    /// Refuses queued effects after the exact original clock, without updating persisted old admissions.
    pub(crate) fn check(&self) -> Result<(), String> {
        self.0.check().map(|_| ())
    }
}

/// Closed actual producer responsibility; readers and inbound copies keep their independent journals.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ReplicaContentProducerKind {
    /// Exact original Raft entry retains bytes before append.
    Log,
    /// Actual accepted complete view retains current inputs before application apply.
    View,
    /// One original Snapshot build/publication, distinct from every outgoing reader.
    SnapshotBuild,
    /// One original incoming Snapshot install, never imported from its sender's operational journal.
    SnapshotInstall,
}

/// Complete original parent identity; adapters independently read actual SQL before using any proof.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentProducerSource {
    /// Exact original local storage producer kind.
    pub kind: ReplicaContentProducerKind,
    /// Original actual issuing application/consensus epoch, never a renewed current epoch.
    pub epoch: ControllerEpoch,
    /// Exact original bounded log/view/Snapshot position, not a filesystem path selected by a caller.
    pub position: String,
    /// Complete original candidate/metadata identity, independently of the readable position label.
    pub authority_digest: String,
    /// Complete original exact parent record identity; this is not a publication receipt by itself.
    pub parent_record_digest: String,
    /// Original complete checkpoint identity, absent only for a legitimately empty initial Snapshot.
    pub checkpoint_digest: Option<String>,
}

/// Immutable first producer admission; actual original source inode/process are never supplied by wire.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProducerIntent {
    /// Closed original intent meaning.
    schema: String,
    /// Exact original operation, retained after publication/retirement.
    operation_id: String,
    /// Actual receiving member's original software/backend identity.
    receiver: ReplicaContentIdentity,
    /// Exact original parent source semantics.
    source: ReplicaContentProducerSource,
    /// Independently sampled actual original parent SQL descriptor device.
    sql_device: u64,
    /// Actual original parent SQL inode; another copied database cannot lend proof.
    sql_inode: u64,
    /// Complete original sorted qualified requirements, including equal hashes in different owners.
    inputs: Vec<ArtifactContentKey>,
    /// Original process/kernel/namespace issue and immutable effect duration.
    budget: ContentBudget,
}

/// Exact original source publication observation, separate from metadata labels and current availability.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Publication {
    /// Exact immutable complete original parent SQL observation, produced by the managed adapter.
    proof_digest: String,
    /// Original actual same-kernel completion sample within its original budget.
    completed_ms: u64,
}

/// Explicit local closure; Unknown cannot become unpublished solely because a current head is absent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Resolution {
    /// Actual new original observation process/kernel, independent of the old effect deadline.
    observer: ContentBudget,
    /// Exact original parent/complete-consumer observation, never a caller success boolean.
    proof_digest: String,
    /// Separate actual original-process absence, required when publication outcome was uncertain.
    process_end: Option<ContentProcessEndObservation>,
}

/// Independent readonly source observation, never an invented original completion timestamp.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ParentObservation {
    /// Actual new observation process/kernel, independently of the old effect budget.
    observer: ContentBudget,
    /// Exact complete original parent observation identity supplied by its actual managed adapter.
    proof_digest: String,
    /// Original current retention responsibility, without implying byte or physical completion.
    still_referenced: bool,
}

/// Complete original producer row; both publication and resolution retain immutable first evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProducerRecord {
    /// Complete first admission before effects.
    intent: ProducerIntent,
    /// Exact original actual publication; no readonly reconciliation repeats its effect.
    publication: Option<Publication>,
    /// Actual original managed permit scope closed; this is neither publication nor an OS wait.
    worker_closed_ms: Option<u64>,
    /// Independent first observation; original publication may remain Unknown beside it.
    observation: Option<ParentObservation>,
    /// Exact independent retirement/abort, absent while any original uncertainty remains.
    resolution: Option<Resolution>,
}

/// Bounded readonly original observation; it never admits another copy/build/install or grants quorum.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentProducerStatus {
    /// Explicit original query interpretation.
    pub schema: String,
    /// Original operation ID, never reused after release.
    pub operation_id: String,
    /// Exact first typed intent identity.
    pub intent_digest: String,
    /// Complete original retained evidence, including original process/budget/source SQL.
    pub original: serde_json::Value,
    /// Unknown, published, released or retired-unknown; no current byte/use/physical claim follows.
    pub disposition: String,
}

/// One actual newly admitted worker scope; its original leases remain live through every effect.
pub(crate) struct ProducerPermit {
    /// Original actual receiver storage lifetime, independent of a cancelled caller's future.
    store: ReplicaContentStore,
    /// Immutable first admitted source/process/budget.
    intent: ProducerIntent,
    /// Actual parent writer lifetime, retained by the managed source adapter before handing work off.
    parent: Arc<crate::store::ReplicaWriterLease>,
}

/// Duplicate original IDs return observations only; no second permit or renewed budget is constructed.
pub(crate) enum ProducerAdmission {
    /// Actual first worker may enter its original bounded effects.
    New(Box<ProducerPermit>),
    /// Original known/uncertain history is readonly and cannot be executed again.
    Existing(ReplicaContentProducerStatus),
}

/// Initializes only the explicit fresh/upgrading producer journal inside an original SQL transaction.
pub(super) fn initialize(connection: &Connection, legacy: BTreeSet<String>) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE content_producers(operation_id TEXT PRIMARY KEY,record TEXT NOT NULL)",
        )
        .map_err(|error| error.to_string())?;
    layout::write_head(connection, "producer_ids", &BTreeSet::new())?;
    layout::write_head(connection, "legacy_producer_ids", &legacy)
}

/// Converts the actual original source kind into its independent byte-retention responsibility.
fn reference_kind(kind: ReplicaContentProducerKind) -> ReplicaContentReferenceKind {
    match kind {
        ReplicaContentProducerKind::Log => ReplicaContentReferenceKind::Log,
        ReplicaContentProducerKind::View => ReplicaContentReferenceKind::View,
        ReplicaContentProducerKind::SnapshotBuild | ReplicaContentProducerKind::SnapshotInstall => {
            ReplicaContentReferenceKind::Snapshot
        }
    }
}

/// Derives one complete original operation's own pins, without borrowing another producer's identity.
fn references(intent: &ProducerIntent) -> Result<Vec<ReplicaContentReference>, String> {
    intent
        .inputs
        .iter()
        .map(|content| {
            Ok(ReplicaContentReference {
                reference_id: format!(
                    "producer:{}:{}",
                    digest(&intent.operation_id)?,
                    digest(content)?
                ),
                kind: reference_kind(intent.source.kind),
                content: content.clone(),
                authority_digest: digest(intent)?,
                generation: 1,
            })
        })
        .collect()
}

/// Reads one bounded exact original operation; absent row never implies permission to repeat.
fn record(connection: &Connection, id: &str) -> Result<Option<ProducerRecord>, String> {
    use rusqlite::OptionalExtension;
    let raw: Option<String> = connection
        .query_row(
            "SELECT record FROM content_producers WHERE operation_id=?1",
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    raw.map(|raw| {
        if raw.len() > 8 * 1024 * 1024 {
            return Err("original content producer record exceeds bound".into());
        }
        serde_json::from_str(&raw).map_err(|error| error.to_string())
    })
    .transpose()
}

/// Serializes immutable original evidence without reading bytes, renewing budget or mutating receipts.
fn status(record: &ProducerRecord) -> Result<ReplicaContentProducerStatus, String> {
    Ok(ReplicaContentProducerStatus {
        schema: "roboguide.content-producer-observation/v0.1".into(),
        operation_id: record.intent.operation_id.clone(),
        intent_digest: digest(&record.intent)?,
        original: serde_json::to_value(record).map_err(|error| error.to_string())?,
        disposition: match (record.publication.is_some(), record.resolution.is_some()) {
            (false, false) => "unknown",
            (true, false) => "published",
            (true, true) => "released",
            (false, true) => "retired-unknown",
        }
        .into(),
    })
}

/// Requires exact bounded parent identity before an actual original source descriptor can admit work.
fn validate_source(source: &ReplicaContentProducerSource) -> Result<(), String> {
    if source.epoch.term == 0
        || source.epoch.replica_id == 0
        || source.position.is_empty()
        || source.position.len() > 512
        || source.position.chars().any(char::is_control)
        || !crate::content::hex_digest(&source.authority_digest)
        || !crate::content::hex_digest(&source.parent_record_digest)
        || source
            .checkpoint_digest
            .as_deref()
            .is_some_and(|value| !crate::content::hex_digest(value))
    {
        return Err("original content producer source is incomplete or unbounded".into());
    }
    Ok(())
}

/// Validates mandatory complete producer and legacy inventories beside all original exact pins.
pub(super) fn validate_records(
    connection: &Connection,
    identity: &ReplicaContentIdentity,
) -> Result<(), String> {
    let heads = layout::head(connection, "producer_ids")?;
    if heads.len() > 4096 {
        return Err("original content producer history reached its bound".into());
    }
    let mut pins = BTreeSet::new();
    for id in &heads {
        let original =
            record(connection, id)?.ok_or("original producer head lost its operation row")?;
        let intent = &original.intent;
        validate_source(&intent.source)?;
        intent.budget.validate()?;
        if intent.schema != "roboguide.content-producer-intent/v0.1"
            || intent.receiver != *identity
            || intent.operation_id.is_empty()
            || intent.operation_id.len() > 128
            || digest(&intent.operation_id)? != *id
            || intent.sql_inode == 0
            || intent.inputs.len() > 16_384
            || intent.inputs.windows(2).any(|keys| keys[0] >= keys[1])
        {
            return Err("original producer owner/SQL/command/input identity differs".into());
        }
        for key in &intent.inputs {
            key.validate()?;
        }
        if intent.inputs.windows(2).any(|keys| {
            keys[0].source_owner == keys[1].source_owner
                && keys[0].content_digest == keys[1].content_digest
        }) {
            return Err("original producer has conflicting qualified content sizes".into());
        }
        if original.publication.as_ref().is_some_and(|receipt| {
            !crate::content::hex_digest(&receipt.proof_digest)
                || !intent.budget.validates_completion(receipt.completed_ms)
        }) {
            return Err(
                "original producer publication lacks its immutable actual completion sample".into(),
            );
        }
        if original
            .worker_closed_ms
            .is_some_and(|sample| !intent.budget.validates_close(sample))
        {
            return Err("original producer scope close changed its actual original clock".into());
        }
        if let Some(observation) = &original.observation {
            observation.observer.validate()?;
            if !crate::content::hex_digest(&observation.proof_digest) {
                return Err("original producer source observation lacks its exact proof".into());
            }
        }
        if let Some(resolution) = &original.resolution {
            resolution.observer.validate()?;
            if !crate::content::hex_digest(&resolution.proof_digest)
                || (original.publication.is_none()
                    && original.worker_closed_ms.is_none()
                    && resolution
                        .process_end
                        .as_ref()
                        .is_none_or(|end| !intent.budget.validates_end(end)))
            {
                return Err(
                    "original uncertain producer release lost actual original process-end proof"
                        .into(),
                );
            }
        }
        for expected in references(intent)? {
            let pin = reference_record(connection, &expected.reference_id)?
                .ok_or("original producer lost its independent input pin")?;
            if pin.reference != expected
                || pin.active != original.resolution.is_none()
                || (original.resolution.is_some()
                    && pin.release_digest.as_deref() != Some(digest(&original)?.as_str()))
            {
                return Err(
                    "original producer pin release differs from its exact retained operation"
                        .into(),
                );
            }
            pins.insert(expected.reference_id);
        }
    }
    let legacy = layout::head(connection, "legacy_producer_ids")?;
    for id in &legacy {
        let pin = reference_record(connection, id)?
            .ok_or("original legacy producer inventory lost its retained row")?;
        if !matches!(
            pin.reference.kind,
            ReplicaContentReferenceKind::Log
                | ReplicaContentReferenceKind::View
                | ReplicaContentReferenceKind::Snapshot
        ) || id.starts_with("producer:")
        {
            return Err(
                "legacy producer inventory changed its unavailable original history".into(),
            );
        }
    }
    for id in layout::head(connection, "reference_ids")? {
        let pin =
            reference_record(connection, &id)?.ok_or("original producer input pin disappeared")?;
        if matches!(
            pin.reference.kind,
            ReplicaContentReferenceKind::Log
                | ReplicaContentReferenceKind::View
                | ReplicaContentReferenceKind::Snapshot
        ) && !pins.contains(&id)
            && !legacy.contains(&id)
        {
            return Err(
                "original producer reference has no complete journal or explicit legacy inventory"
                    .into(),
            );
        }
    }
    Ok(())
}

impl ReplicaContentStore {
    /// Hashes complete retained legacy rows without assigning any of them to a particular parent.
    pub(crate) fn legacy_producer_inventory_digest(&self) -> Result<String, String> {
        let guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_ref()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        let mut originals = Vec::new();
        for id in layout::head(connection, "legacy_producer_ids")? {
            originals.push((
                id.clone(),
                reference_record(connection, &id)?
                    .ok_or("original legacy producer row disappeared")?,
            ));
        }
        digest(&originals)
    }

    /// Reports only this actually opened explicit generation, not global reference or deployment coverage.
    pub(crate) fn producer_journal_enabled(&self) -> bool {
        self.inner.producers
    }

    /// Reads bounded complete original producer histories for the exact actual managed parent descriptor.
    pub(crate) fn producers_for_parent(
        &self,
        parent: &crate::store::ReplicaWriterLease,
    ) -> Result<Vec<ReplicaContentProducerStatus>, String> {
        if !self.inner.producers {
            return Err("original reader profile has no producer journal authority".into());
        }
        let actual = parent.identity()?;
        let guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_ref()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        let parents = super::restore::parent_sources(&self.inner, connection, actual)?;
        let mut result = Vec::new();
        for id in layout::head(connection, "producer_ids")? {
            let original = record(connection, &id)?.ok_or("original producer head lost its row")?;
            if parents.contains(&(original.intent.sql_device, original.intent.sql_inode)) {
                result.push(status(&original)?);
            }
        }
        Ok(result)
    }

    /// Stores a first exact source observation or retires only this original proven-removed producer.
    /// Its actual parent adapter holds the original writer and checks every current responsibility first.
    pub(crate) fn reconcile_producer(
        &self,
        operation_id: &str,
        parent: &crate::store::ReplicaWriterLease,
        still_referenced: bool,
        proof_digest: &str,
    ) -> Result<ReplicaContentProducerStatus, String> {
        if !self.inner.producers || !crate::content::hex_digest(proof_digest) {
            return Err("original producer reconciliation lacks its exact profile/proof".into());
        }
        let actual = parent.identity()?;
        let observer = ContentBudget::issue(300_000)?;
        let mut guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_mut()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        let id = digest(&operation_id)?;
        let mut original =
            record(&transaction, &id)?.ok_or("original producer operation is unavailable")?;
        if !super::restore::matches_parent(
            &self.inner,
            &transaction,
            actual,
            (original.intent.sql_device, original.intent.sql_inode),
        )? {
            return Err(
                "original producer reconciliation belongs to another actual SQL source".into(),
            );
        }
        if original.resolution.is_some() {
            return status(&original);
        }
        if still_referenced && original.observation.is_some() {
            return status(&original);
        }
        let process_end = if !still_referenced
            && original.publication.is_none()
            && original.worker_closed_ms.is_none()
        {
            match original.intent.budget.try_observe_process_end()? {
                Some(end) => Some(end),
                None => return status(&original),
            }
        } else {
            None
        };
        if original.observation.is_none() {
            original.observation = Some(ParentObservation {
                observer: observer.clone(),
                proof_digest: proof_digest.into(),
                still_referenced,
            });
        }
        if !still_referenced {
            original.resolution = Some(Resolution {
                observer,
                proof_digest: proof_digest.into(),
                process_end,
            });
            let release = digest(&original)?;
            for expected in references(&original.intent)? {
                let mut pin = reference_record(&transaction, &expected.reference_id)?
                    .ok_or("original producer lost its exact input pin")?;
                if pin.reference != expected || !pin.active {
                    return Err("original producer ownership changed before retirement".into());
                }
                pin.active = false;
                pin.release_digest = Some(release.clone());
                transaction
                    .execute(
                        "UPDATE content_references SET record=?2 WHERE reference_id=?1",
                        params![
                            expected.reference_id,
                            serde_json::to_string(&pin).map_err(|error| error.to_string())?
                        ],
                    )
                    .map_err(|error| error.to_string())?;
            }
        }
        transaction
            .execute(
                "UPDATE content_producers SET record=?2 WHERE operation_id=?1",
                params![
                    id,
                    serde_json::to_string(&original).map_err(|error| error.to_string())?
                ],
            )
            .map_err(|error| error.to_string())?;
        layout::within_budget(&transaction)?;
        transaction.commit().map_err(|error| error.to_string())?;
        status(&original)
    }

    /// Observes only an existing original producer under the explicit complete journal profile.
    pub fn producer_status(
        &self,
        operation_id: &str,
    ) -> Result<Option<ReplicaContentProducerStatus>, String> {
        if !self.inner.producers {
            return Err("original reader profile has no producer journal authority".into());
        }
        let guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_ref()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        record(connection, &digest(&operation_id)?)?
            .as_ref()
            .map(status)
            .transpose()
    }

    /// Persists actual source SQL/process/budget and complete original pins before handing out one permit.
    pub(crate) fn admit_producer(
        &self,
        operation_id: &str,
        source: ReplicaContentProducerSource,
        parent: Arc<crate::store::ReplicaWriterLease>,
        inputs: &[ArtifactContentKey],
        duration_ms: u64,
        original_clock: Option<&ProducerClock>,
    ) -> Result<ProducerAdmission, String> {
        if !self.inner.producers {
            return Err(
                "producer admission requires the explicit original producer profile".into(),
            );
        }
        validate_source(&source)?;
        if operation_id.is_empty()
            || operation_id.len() > 128
            || inputs.len() > 16_384
            || inputs.windows(2).any(|keys| keys[0] >= keys[1])
        {
            return Err("original producer command/input bounds differ".into());
        }
        if inputs.windows(2).any(|keys| {
            keys[0].source_owner == keys[1].source_owner
                && keys[0].content_digest == keys[1].content_digest
        }) {
            return Err("original producer has conflicting qualified content sizes".into());
        }
        for key in inputs {
            key.validate()?;
        }
        let budget = match original_clock {
            Some(clock) if clock.0.duration_ms == duration_ms => {
                clock.check()?;
                clock.0.clone()
            }
            Some(_) => {
                return Err("original queued producer budget cannot change its duration".into());
            }
            None => ContentBudget::issue(duration_ms)?,
        };
        let (sql_device, sql_inode) = parent.identity()?;
        let intent = ProducerIntent {
            schema: "roboguide.content-producer-intent/v0.1".into(),
            operation_id: operation_id.into(),
            receiver: self.identity().clone(),
            source,
            sql_device,
            sql_inode,
            inputs: inputs.into(),
            budget,
        };
        let mut guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_mut()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        let id = digest(&operation_id)?;
        if let Some(original) = record(connection, &id)? {
            if original.intent.source != intent.source
                || original.intent.inputs != intent.inputs
                || original.intent.sql_device != intent.sql_device
                || original.intent.sql_inode != intent.sql_inode
                || original.intent.budget.duration_ms != duration_ms
            {
                return Err(
                    "original producer ID has different immutable source/input/budget".into(),
                );
            }
            return Ok(ProducerAdmission::Existing(status(&original)?));
        }
        intent.budget.check()?;
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        let mut heads = layout::head(&transaction, "producer_ids")?;
        let mut pins = layout::head(&transaction, "reference_ids")?;
        if heads.len() >= 4096 {
            return Err("original producer history reached its fixed bound".into());
        }
        let original = ProducerRecord {
            intent: intent.clone(),
            publication: None,
            worker_closed_ms: None,
            observation: None,
            resolution: None,
        };
        let raw = serde_json::to_string(&original).map_err(|error| error.to_string())?;
        if raw.len() > 8 * 1024 * 1024 {
            return Err("original producer record exceeds its bound".into());
        }
        transaction
            .execute(
                "INSERT INTO content_producers VALUES (?1,?2)",
                params![id, raw],
            )
            .map_err(|error| error.to_string())?;
        for pin in references(&intent)? {
            transaction
                .execute(
                    "INSERT INTO content_references VALUES (?1,?2)",
                    params![
                        pin.reference_id,
                        serde_json::to_string(&ReferenceRecord {
                            reference: pin.clone(),
                            active: true,
                            release_digest: None
                        })
                        .map_err(|error| error.to_string())?
                    ],
                )
                .map_err(|error| error.to_string())?;
            pins.insert(pin.reference_id);
        }
        heads.insert(id);
        layout::write_head(&transaction, "producer_ids", &heads)?;
        layout::write_head(&transaction, "reference_ids", &pins)?;
        layout::within_budget(&transaction)?;
        intent.budget.check()?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(ProducerAdmission::New(Box::new(ProducerPermit {
            store: self.clone(),
            intent,
            parent,
        })))
    }
}

impl ProducerPermit {
    /// Exposes only the complete original source used for an independent actual parent SQL check.
    pub(crate) fn source(&self) -> &ReplicaContentProducerSource {
        &self.intent.source
    }

    /// Returns the exact original operation identity without admitting another effect.
    pub(crate) fn operation_id(&self) -> &str {
        &self.intent.operation_id
    }

    /// Checks this original worker's nonrenewing actual budget after queueing and before every effect.
    pub(crate) fn check(&self) -> Result<(), String> {
        if self.parent.identity()? != (self.intent.sql_device, self.intent.sql_inode) {
            return Err("original producer actual source SQL descriptor changed".into());
        }
        self.intent.budget.check().map(|_| ())
    }

    /// Records an independently verified original parent publication; dropping on error preserves Unknown.
    pub(crate) fn published(
        self,
        proof_digest: &str,
    ) -> Result<ReplicaContentProducerStatus, String> {
        if !crate::content::hex_digest(proof_digest) {
            return Err("original producer lacks actual source lifetime/publication proof".into());
        }
        self.check()?;
        let mut guard = self
            .store
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_mut()
            .ok_or("original content connection is closed")?;
        self.store.validate_connection(connection)?;
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        let id = digest(&self.intent.operation_id)?;
        let mut original =
            record(&transaction, &id)?.ok_or("original producer admission disappeared")?;
        if original.intent != self.intent
            || original.publication.is_some()
            || original.resolution.is_some()
        {
            return Err("original producer changed while its actual worker was live".into());
        }
        original.publication = Some(Publication {
            proof_digest: proof_digest.into(),
            completed_ms: self.intent.budget.check()?,
        });
        original.worker_closed_ms = Some(self.intent.budget.closed_now()?);
        transaction
            .execute(
                "UPDATE content_producers SET record=?2 WHERE operation_id=?1",
                params![
                    id,
                    serde_json::to_string(&original).map_err(|error| error.to_string())?
                ],
            )
            .map_err(|error| error.to_string())?;
        layout::within_budget(&transaction)?;
        self.check()?;
        transaction.commit().map_err(|error| error.to_string())?;
        status(&original)
    }
}

impl Drop for ProducerPermit {
    /// Records only the actual original managed scope close; errors keep Unknown and its pins.
    /// The exact parent writer stays owned until after this attempt; no OS wait or publication is invented.
    fn drop(&mut self) {
        let observe = (|| -> Result<(), String> {
            if self.parent.identity()? != (self.intent.sql_device, self.intent.sql_inode) {
                return Err("original producer source descriptor changed before close".into());
            }
            let closed = self.intent.budget.closed_now()?;
            let mut guard = self
                .store
                .inner
                .connection
                .lock()
                .map_err(|error| error.to_string())?;
            let connection = guard
                .as_mut()
                .ok_or("original content connection is closed")?;
            self.store.validate_connection(connection)?;
            let transaction = connection
                .transaction()
                .map_err(|error| error.to_string())?;
            let id = digest(&self.intent.operation_id)?;
            let mut original = record(&transaction, &id)?
                .ok_or("original producer admission disappeared before scope close")?;
            if original.intent != self.intent {
                return Err("original producer source changed before scope close".into());
            }
            if original.worker_closed_ms.is_some() || original.resolution.is_some() {
                return Ok(());
            }
            original.worker_closed_ms = Some(closed);
            transaction
                .execute(
                    "UPDATE content_producers SET record=?2 WHERE operation_id=?1",
                    params![
                        id,
                        serde_json::to_string(&original).map_err(|error| error.to_string())?
                    ],
                )
                .map_err(|error| error.to_string())?;
            layout::within_budget(&transaction)?;
            transaction.commit().map_err(|error| error.to_string())
        })();
        if observe.is_err() { /* Original unresolved ownership remains durable rather than being discarded. */
        }
    }
}
