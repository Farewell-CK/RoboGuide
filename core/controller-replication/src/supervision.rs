//! Member-local original Controller process journal; OS exit never establishes device stopping.

use crate::store::{read_meta, write_meta};
use crate::*;
use rusqlite::TransactionBehavior;

/// Persistent opt-in never regresses to an unowned application-process assumption.
pub(crate) const SUPERVISION_KEY: &str = "controller_supervision_v1";
/// Complete bounded original spawn/wait history, independent of Raft application state.
pub(crate) const PROCESS_HISTORY_KEY: &str = "controller_processes_v1";
/// Legacy application authority without original child evidence cannot become an empty live set.
pub(crate) const LEGACY_CONTINUITY_KEY: &str = "legacy_controller_continuity_unknown";
/// Original processes are never discarded to obtain another operation slot.
const MAX_PROCESSES: usize = 1024;

/// Actual original owned process identity; neither PID reuse nor restart can replace it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ControllerProcessIdentity {
    /// Random operation identity fixed before spawn.
    pub instance_id: String,
    /// Actual supervisor process incarnation fixed on Replica construction.
    pub supervisor_instance: String,
    /// Original application epoch, never refreshed within a live process.
    pub epoch: ControllerEpoch,
    /// Original PID observed from the owned Child handle.
    pub pid: u32,
    /// Actual Linux boot identity, not a distributed clock claim.
    pub boot_id: String,
    /// Actual original Linux process start ticks, guarding PID reuse in inspection.
    pub start_ticks: u64,
}

/// Original actual wait outcome; signal termination is not fabricated as a normal exit code.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ControllerProcessExit {
    /// Complete original owned process.
    pub process: ControllerProcessIdentity,
    /// Actual wait exit code when available.
    pub exit_code: Option<i32>,
    /// Actual wait signal when available.
    pub signal: Option<i32>,
}

/// Infrastructure-only source drain evidence carried by the explicit V3 transfer command.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
pub enum ControllerDrainProof {
    /// This actual Replica incarnation has no owned application process and no uncertain history.
    NoApplicationProcess {
        /// Actual process incarnation making this scoped empty-process observation.
        supervisor_instance: String,
        /// Exact original source authority.
        epoch: ControllerEpoch,
    },
    /// The original supervisor reaped its exact original child and durably retained the outcome.
    ProcessExit {
        /// Independent original wait outcome, never a command or terminal receipt.
        outcome: ControllerProcessExit,
    },
}

/// Retained pre-spawn operation through actual PID registration and original OS wait.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ControllerProcessRecord {
    /// Original pre-spawn identity.
    instance_id: String,
    /// Actual owning supervisor incarnation.
    supervisor_instance: String,
    /// Original epoch before spawn.
    epoch: ControllerEpoch,
    /// Actual PID/start identity, unavailable before spawn.
    process: Option<ControllerProcessIdentity>,
    /// Original OS wait, unavailable while ambiguous or active.
    outcome: Option<ControllerProcessExit>,
}

/// Reads a real random Linux UUID, failing without substituting a predictable or invented identity.
pub(crate) fn new_process_instance() -> Result<String, String> {
    let instance = std::fs::read_to_string("/proc/sys/kernel/random/uuid")
        .map_err(|error| error.to_string())?
        .trim()
        .to_string();
    if !valid_instance(&instance) {
        return Err("actual process UUID is unavailable".into());
    }
    Ok(instance)
}

/// Checks exact UUID syntax without treating it as hardware or manufacturer attestation.
fn valid_instance(instance: &str) -> bool {
    instance.len() == 36
        && instance.bytes().enumerate().all(|(index, byte)| {
            if [8, 13, 18, 23].contains(&index) {
                byte == b'-'
            } else {
                byte.is_ascii_hexdigit()
            }
        })
}

/// Validates immutable local identity without probing another process or declaring exit.
pub(crate) fn validate_process(process: &ControllerProcessIdentity) -> Result<(), String> {
    if !valid_instance(&process.instance_id)
        || !valid_instance(&process.supervisor_instance)
        || !valid_instance(&process.boot_id)
        || process.pid == 0
        || process.start_ticks == 0
        || process.epoch.term == 0
        || process.epoch.replica_id == 0
    {
        return Err("original Controller process identity is unavailable".into());
    }
    Ok(())
}

impl ControllerDrainProof {
    /// Validates complete original source authority without promoting software exit to physical proof.
    pub(crate) fn validate(&self, epoch: ControllerEpoch) -> Result<(), String> {
        match self {
            Self::NoApplicationProcess {
                supervisor_instance,
                epoch: original,
            } if valid_instance(supervisor_instance) && *original == epoch => Ok(()),
            Self::ProcessExit { outcome } => {
                validate_process(&outcome.process)?;
                if outcome.process.epoch != epoch
                    || outcome.exit_code.is_some() == outcome.signal.is_some()
                    || outcome.signal.is_some_and(|signal| signal <= 0)
                {
                    return Err("original Controller wait evidence differs or is incomplete".into());
                }
                Ok(())
            }
            _ => Err("original source drain evidence is invalid".into()),
        }
    }
}

/// Reads and validates original process history; missing metadata never becomes an empty live set.
fn history(connection: &rusqlite::Connection) -> Result<Vec<ControllerProcessRecord>, String> {
    let records: Vec<ControllerProcessRecord> = read_meta(connection, PROCESS_HISTORY_KEY)
        .map_err(|error| error.to_string())?
        .ok_or("original Controller process journal is absent")?;
    if records.len() > MAX_PROCESSES {
        return Err("original Controller process history exceeds its bound".into());
    }
    let mut instances = BTreeSet::new();
    for record in &records {
        if !valid_instance(&record.instance_id)
            || !valid_instance(&record.supervisor_instance)
            || record.epoch.term == 0
            || record.epoch.replica_id == 0
            || !instances.insert(&record.instance_id)
        {
            return Err("original Controller spawn identity is invalid or repeated".into());
        }
        if let Some(process) = &record.process {
            validate_process(process)?;
            if process.instance_id != record.instance_id
                || process.supervisor_instance != record.supervisor_instance
                || process.epoch != record.epoch
            {
                return Err("original Controller PID identity differs from spawn intent".into());
            }
        }
        if let Some(outcome) = &record.outcome {
            if record.process.as_ref() != Some(&outcome.process) {
                return Err("original Controller wait differs from registered PID".into());
            }
            ControllerDrainProof::ProcessExit {
                outcome: outcome.clone(),
            }
            .validate(record.epoch)?;
        }
    }
    if records
        .iter()
        .filter(|record| record.outcome.is_none())
        .count()
        > 1
    {
        return Err("original Controller journal contains overlapping uncertain processes".into());
    }
    Ok(records)
}

/// Requires exact retained original spawn/PID identity even when its genuine wait is already known.
pub(crate) fn require_retained_process(
    connection: &rusqlite::Connection,
    process: &ControllerProcessIdentity,
) -> Result<(), String> {
    if !history(connection)?
        .iter()
        .any(|record| record.process.as_ref() == Some(process))
    {
        return Err("original Controller ingress lost its exact retained process journal".into());
    }
    Ok(())
}

/// Requires the actual Unix peer to be the still-live original registered child of this supervisor.
pub(crate) fn require_original_peer(
    connection: &rusqlite::Connection,
    supervisor: &str,
    epoch: ControllerEpoch,
    instance: &str,
    peer_pid: u32,
) -> Result<ControllerProcessIdentity, String> {
    if read_meta::<bool>(connection, SUPERVISION_KEY).map_err(|error| error.to_string())?
        != Some(true)
        || read_meta::<bool>(connection, LEGACY_CONTINUITY_KEY)
            .map_err(|error| error.to_string())?
            != Some(false)
    {
        return Err("original Controller supervision authority is unavailable".into());
    }
    let records = history(connection)?;
    let original = records
        .last()
        .filter(|record| record.outcome.is_none())
        .and_then(|record| record.process.as_ref())
        .ok_or("actual original Controller process has no live registered incarnation")?;
    if original.supervisor_instance != supervisor
        || original.epoch != epoch
        || original.instance_id != instance
        || original.pid != peer_pid
    {
        return Err("actual Unix peer differs from the original owned Controller process".into());
    }
    let stat = std::fs::read_to_string(format!("/proc/{peer_pid}/stat"))
        .map_err(|error| format!("actual original Controller process unavailable: {error}"))?;
    let (_, tail) = stat
        .rsplit_once(')')
        .ok_or("actual original Controller stat unavailable")?;
    let fields = tail.split_whitespace().collect::<Vec<_>>();
    if fields
        .first()
        .is_none_or(|state| matches!(*state, "Z" | "X" | "x"))
        || fields.get(19).and_then(|value| value.parse::<u64>().ok()) != Some(original.start_ticks)
        || std::fs::read_to_string("/proc/sys/kernel/random/boot_id")
            .map_err(|error| error.to_string())?
            .trim()
            != original.boot_id
    {
        return Err("actual original Controller incarnation ended or changed".into());
    }
    Ok(original.clone())
}

impl SqliteRaftStore {
    /// Allows stale IPC cleanup only with exact retained original wait coverage; socket loss grants none.
    pub(crate) fn require_original_endpoint_reopen(&self) -> Result<(), String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        Self::validate_controller_supervision(&connection)?;
        if history(&connection)?
            .iter()
            .any(|record| record.outcome.is_none())
        {
            return Err(
                "original child endpoint cannot replace an unreaped or spawning incarnation".into(),
            );
        }
        Ok(())
    }
    /// Requires retained original wait evidence before offline adoption can change parent generation.
    pub(crate) fn require_adoption_children_closed(
        connection: &rusqlite::Connection,
    ) -> Result<(), String> {
        Self::validate_controller_supervision(connection)?;
        if read_meta::<bool>(connection, LEGACY_CONTINUITY_KEY)
            .map_err(|error| error.to_string())?
            != Some(false)
            || history(connection)?
                .iter()
                .any(|record| record.outcome.is_none())
        {
            return Err(
                "original Controller child continuity/actual wait is unavailable for adoption"
                    .into(),
            );
        }
        Ok(())
    }

    /// Validates the complete process journal during format reopen, before granting writer authority.
    pub(crate) fn validate_controller_supervision(
        connection: &rusqlite::Connection,
    ) -> Result<(), String> {
        let enabled: bool = read_meta(connection, SUPERVISION_KEY)
            .map_err(|error| error.to_string())?
            .ok_or("original Controller supervision marker is absent")?;
        let records = history(connection)?;
        read_meta::<bool>(connection, LEGACY_CONTINUITY_KEY)
            .map_err(|error| error.to_string())?
            .ok_or("original Controller continuity marker is absent")?;
        if !enabled && !records.is_empty() {
            return Err("unowned Replica cannot retain a supervised process".into());
        }
        Ok(())
    }

    /// Permanently opts this actual store into owned-Controller supervision before any child starts.
    pub(crate) fn enable_controller_supervision(&self) -> Result<(), String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        Self::validate_controller_supervision(&connection)?;
        if read_meta::<bool>(&connection, LEGACY_CONTINUITY_KEY)
            .map_err(|error| error.to_string())?
            != Some(false)
        {
            return Err(
                "legacy Controller process continuity requires independent migration evidence"
                    .into(),
            );
        }
        write_meta(&connection, SUPERVISION_KEY, &true).map_err(|error| error.to_string())
    }

    /// Persists original pre-spawn intent; uncertain old processes retain capacity across restart.
    pub(crate) fn begin_controller_process(
        &self,
        epoch: ControllerEpoch,
        supervisor_instance: &str,
    ) -> Result<String, String> {
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        if read_meta::<bool>(&transaction, SUPERVISION_KEY).map_err(|error| error.to_string())?
            != Some(true)
        {
            return Err("Controller spawn requires its original supervised store".into());
        }
        let mut records = history(&transaction)?;
        if read_meta::<Vec<LeaderTransferRecord>>(&transaction, "leader_transfers_v2")
            .map_err(|error| error.to_string())?
            .is_none_or(|records| {
                records
                    .iter()
                    .any(|record| !matches!(record.state.as_str(), "completed" | "rejected"))
            })
        {
            return Err("Controller spawn is fenced by an original pending transfer".into());
        }
        if records.iter().any(|record| record.outcome.is_none()) || records.len() >= MAX_PROCESSES {
            return Err(
                "original Controller process still requires wait reconciliation or history is full"
                    .into(),
            );
        }
        let instance_id = new_process_instance()?;
        records.push(ControllerProcessRecord {
            instance_id: instance_id.clone(),
            supervisor_instance: supervisor_instance.into(),
            epoch,
            process: None,
            outcome: None,
        });
        write_meta(&transaction, PROCESS_HISTORY_KEY, &records)
            .map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(instance_id)
    }

    /// Binds an actual original owned PID before application work can be treated as supervised.
    pub(crate) fn register_controller_process(
        &self,
        process: ControllerProcessIdentity,
    ) -> Result<(), String> {
        validate_process(&process)?;
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        let mut records = history(&transaction)?;
        let original = records
            .last_mut()
            .ok_or("original Controller spawn intent is absent")?;
        if original.instance_id != process.instance_id
            || original.supervisor_instance != process.supervisor_instance
            || original.epoch != process.epoch
            || original.outcome.is_some()
            || original
                .process
                .as_ref()
                .is_some_and(|identity| identity != &process)
        {
            return Err("actual Controller PID differs from original spawn intent".into());
        }
        original.process = Some(process);
        write_meta(&transaction, PROCESS_HISTORY_KEY, &records)
            .map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())
    }

    /// Retains only an exact original supervisor wait, refusing another incarnation or replacement PID.
    pub(crate) fn record_controller_exit(
        &self,
        outcome: ControllerProcessExit,
        supervisor_instance: &str,
    ) -> Result<(), String> {
        ControllerDrainProof::ProcessExit {
            outcome: outcome.clone(),
        }
        .validate(outcome.process.epoch)?;
        if outcome.process.supervisor_instance != supervisor_instance {
            return Err("another supervisor cannot assert original Child.wait".into());
        }
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        let mut records = history(&transaction)?;
        let original = records
            .iter_mut()
            .find(|record| record.instance_id == outcome.process.instance_id)
            .ok_or("original Controller process is absent")?;
        if original.process.as_ref() != Some(&outcome.process)
            || original
                .outcome
                .as_ref()
                .is_some_and(|saved| saved != &outcome)
        {
            return Err("original Controller wait identity/outcome differs".into());
        }
        original.outcome = Some(outcome);
        write_meta(&transaction, PROCESS_HISTORY_KEY, &records)
            .map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())
    }

    /// Freezes the exact original source process or refuses an unreaped process from another epoch.
    pub(crate) fn transfer_source_process(
        &self,
        epoch: ControllerEpoch,
    ) -> Result<Option<ControllerProcessIdentity>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        if read_meta::<bool>(&connection, LEGACY_CONTINUITY_KEY)
            .map_err(|error| error.to_string())?
            != Some(false)
        {
            return Err("legacy Controller process continuity is unknown".into());
        }
        let records = history(&connection)?;
        if records.iter().any(|record| {
            record.outcome.is_none() && (record.epoch != epoch || record.process.is_none())
        }) {
            return Err(
                "original Controller process requires independent wait reconciliation".into(),
            );
        }
        Ok(records
            .iter()
            .rev()
            .find(|record| record.epoch == epoch)
            .and_then(|record| record.process.clone()))
    }

    /// Reads original actual wait evidence without killing, polling a replacement PID or renewing effects.
    pub(crate) fn controller_drain_proof(
        &self,
        epoch: ControllerEpoch,
        original: Option<&ControllerProcessIdentity>,
        supervisor_instance: &str,
    ) -> Result<Option<ControllerDrainProof>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let records = history(&connection)?;
        if let Some(original) = original {
            return Ok(records
                .iter()
                .find(|record| record.process.as_ref() == Some(original))
                .and_then(|record| record.outcome.clone())
                .map(|outcome| ControllerDrainProof::ProcessExit { outcome }));
        }
        if records.iter().any(|record| record.outcome.is_none()) {
            return Ok(None);
        }
        Ok(Some(ControllerDrainProof::NoApplicationProcess {
            supervisor_instance: supervisor_instance.into(),
            epoch,
        }))
    }
}

impl ReplicaNode {
    /// Enables actual owned-process journaling; once enabled, a store never falls back to unowned mode.
    pub fn enable_controller_supervision(&self) -> Result<(), String> {
        self.store.enable_controller_supervision()
    }

    /// Persists the original pre-spawn operation under current unfenced application authority.
    pub async fn begin_controller_process(&self, epoch: ControllerEpoch) -> Result<String, String> {
        self.check_epoch(epoch).await?;
        self.store
            .begin_controller_process(epoch, &self.process_instance)
    }

    /// Reads actual Linux identity from an owned Child PID and binds its immutable original operation.
    pub fn register_controller_process(
        &self,
        epoch: ControllerEpoch,
        instance_id: String,
        pid: u32,
    ) -> Result<ControllerProcessIdentity, String> {
        let stat = std::fs::read_to_string(format!("/proc/{pid}/stat"))
            .map_err(|error| error.to_string())?;
        let start_ticks = stat
            .rsplit_once(')')
            .and_then(|(_, tail)| tail.split_whitespace().nth(19))
            .ok_or("actual Controller start ticks are unavailable")?
            .parse::<u64>()
            .map_err(|error| error.to_string())?;
        let process = ControllerProcessIdentity {
            instance_id,
            supervisor_instance: self.process_instance.clone(),
            epoch,
            pid,
            boot_id: std::fs::read_to_string("/proc/sys/kernel/random/boot_id")
                .map_err(|error| error.to_string())?
                .trim()
                .into(),
            start_ticks,
        };
        self.store.register_controller_process(process.clone())?;
        Ok(process)
    }

    /// Commits the actual original Child.wait result supplied by this exact supervisor incarnation.
    pub fn record_controller_exit(&self, outcome: ControllerProcessExit) -> Result<(), String> {
        self.store
            .record_controller_exit(outcome, &self.process_instance)
    }
}
