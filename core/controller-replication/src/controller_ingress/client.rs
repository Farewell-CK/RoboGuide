//! Synchronous original application transaction port over actual private Unix mutual TLS.

use super::wire::{ChildCommand, ChildReply, ChildRequest};
use super::*;
use crate::transport_budget::{MAX_FRAME_BYTES, TransportBudget};
use std::io::{Read, Write};
use std::os::unix::fs::MetadataExt;
use std::path::PathBuf;

/// Explicit startup-owned child transport; the actual PID/UID are never supplied through configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ControllerContentClientConfig {
    /// Actual private same-host endpoint under the original supervisor's owned directory.
    pub socket: PathBuf,
    /// Exact original startup application/network profile.
    pub profile: String,
    /// Original immutable issuing epoch.
    pub epoch: ControllerEpoch,
    /// Original pre-spawn operation, independently checked against the actual socket PID.
    pub instance: String,
    /// Actual mutual-TLS material; member identity alone grants no application permission.
    pub tls: ReplicaTlsConfig,
    /// Actual original live parent database supplying committed certificate trust.
    pub trust_database: PathBuf,
    /// Explicit original complete-operation duration, fixed at first admission without retry renewal.
    #[serde(default = "default_budget")]
    pub budget_ms: u64,
}

/// Preserves the original short default while longer deployment budgets require an explicit value.
fn default_budget() -> u64 {
    12000
}

/// Actual source-owning child client; clones retain the same SDK writer and transport capacities.
#[derive(Clone)]
pub struct ControllerContentClient {
    /// Complete immutable original connection/source identity declaration.
    config: ControllerContentClientConfig,
    /// Actual original parent SQL inode, preventing path replacement from changing local trust.
    parent_identity: (u64, u64),
    /// Actual loaded current-trust credentials.
    tls: Arc<crate::tls::TlsMaterial>,
    /// Actual original managed application Artifact writer, including live Prepared sources.
    source: artifact_store::FileSystemArtifactStore,
    /// Actual independent local connection/frame resource admission.
    budget: Arc<TransportBudget>,
}

/// One original complete exchange; its actual scopes and first clock never come from a reply flag.
struct Exchange {
    /// Actual completed mutual-TLS connection over a kernel-authenticated Unix socket.
    stream: rustls::StreamOwned<rustls::ClientConnection, std::os::unix::net::UnixStream>,
    /// Captured actual original leaf chains, retained through bounded source I/O.
    evidence: crate::tls::VerifiedTlsConnection,
    /// Original child-side clock, independent of the receiver's first clock.
    clock: ContentBudget,
    /// Actual outgoing capacity, retained through original response consumption.
    _capacity: tokio::sync::OwnedSemaphorePermit,
}

impl ControllerContentClient {
    /// Opens no service or Node; binds the actual existing source writer and parent inode at startup.
    pub fn new(
        config: ControllerContentClientConfig,
        source: artifact_store::FileSystemArtifactStore,
    ) -> Result<Self, String> {
        if !config.socket.is_absolute()
            || config.socket.as_os_str().len() > 103
            || !config.trust_database.is_absolute()
            || config.epoch.replica_id == 0
            || config.epoch.term == 0
            || config.instance.len() != 36
            || !crate::content::hex_digest(&config.profile)
            || !(100..=300000).contains(&config.budget_ms)
        {
            return Err("invalid explicit original Controller content client".into());
        }
        source
            .writer_identity()
            .map_err(|error| error.to_string())?;
        let actual =
            std::fs::symlink_metadata(&config.trust_database).map_err(|error| error.to_string())?;
        if !actual.is_file() {
            return Err("actual original Controller parent SQL absent".into());
        }
        let parent_identity = (actual.dev(), actual.ino());
        let tls = Arc::new(crate::tls::with_readonly_trust(
            &config.tls,
            &config.trust_database,
            &config.profile,
            config.epoch.replica_id,
        )?);
        Ok(Self {
            config,
            parent_identity,
            tls,
            source,
            budget: TransportBudget::new(ReplicaTransportLimits::default())?,
        })
    }

    /// Checks the actual original backing, source writer, captured TLS and nonrenewable local clock.
    fn check_exchange(&self, exchange: &Exchange) -> Result<(), String> {
        exchange.clock.check()?;
        let actual = std::fs::symlink_metadata(&self.config.trust_database)
            .map_err(|error| error.to_string())?;
        if !actual.is_file() || (actual.dev(), actual.ino()) != self.parent_identity {
            return Err("actual original Controller parent SQL was replaced".into());
        }
        self.source
            .writer_identity()
            .map_err(|error| error.to_string())?;
        if !exchange
            .evidence
            .authorize(self.config.epoch.replica_id, self.config.epoch.replica_id)?
        {
            return Err("actual original Controller connection no longer has permission".into());
        }
        Ok(())
    }

    /// Opens one bounded actual original connection; retry never reuses its first operation as new work.
    fn connect(&self, command: ChildCommand) -> Result<Exchange, String> {
        let clock = ContentBudget::issue(self.config.budget_ms)?;
        let capacity = self.budget.outbound().map_err(|error| error.to_string())?;
        let request = ChildRequest {
            schema: JOURNAL_SCHEMA.into(),
            profile: self.config.profile.clone(),
            epoch: self.config.epoch,
            instance: self.config.instance.clone(),
            budget_ms: self.config.budget_ms,
            command,
        };
        let encoded = crate::transport_budget::encode(&request, &self.budget)
            .map_err(|error| error.to_string())?;
        let actual = std::fs::symlink_metadata(&self.config.trust_database)
            .map_err(|error| error.to_string())?;
        if !actual.is_file() || (actual.dev(), actual.ino()) != self.parent_identity {
            return Err(
                "actual original Controller parent SQL was replaced before connecting".into(),
            );
        }
        let (send, receive) = std::sync::mpsc::sync_channel(1);
        let path = self.config.socket.clone();
        let source = self.source.clone();
        let _connector = std::thread::Builder::new()
            .name("owned-controller-connect".into())
            .spawn(move || {
                let _source = source;
                let result = std::os::unix::net::UnixStream::connect(path)
                    .map(|socket| (socket, capacity))
                    .map_err(|error| error.to_string());
                let _ = send.send(result);
            })
            .map_err(|error| error.to_string())?;
        let (socket, capacity) = loop {
            clock.check()?;
            match receive.recv_timeout(Duration::from_millis(50)) {
                Ok(result) => break result?,
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                Err(error) => return Err(format!("actual original connector ended: {error}")),
            }
        };
        socket
            .set_read_timeout(Some(Duration::from_millis(self.config.budget_ms)))
            .map_err(|error| error.to_string())?;
        socket
            .set_write_timeout(Some(Duration::from_millis(self.config.budget_ms)))
            .map_err(|error| error.to_string())?;
        let tls = self.tls.refreshed()?;
        let name = rustls::pki_types::ServerName::try_from(tls.server_name.clone())
            .map_err(|error| error.to_string())?;
        let mut connection = rustls::ClientConnection::new(tls.client.clone(), name)
            .map_err(|error| error.to_string())?;
        let mut socket = socket;
        while connection.is_handshaking() {
            clock.check()?;
            connection
                .complete_io(&mut socket)
                .map_err(|error| error.to_string())?;
        }
        tls.authenticate_peer(self.config.epoch.replica_id, connection.peer_certificates())?;
        let peer_chain = connection
            .peer_certificates()
            .ok_or("actual original parent chain absent")?
            .iter()
            .map(|certificate| certificate.clone().into_owned())
            .collect();
        let mut exchange = Exchange {
            stream: rustls::StreamOwned::new(connection, socket),
            evidence: crate::tls::VerifiedTlsConnection {
                local: tls,
                peer_chain,
            },
            clock,
            _capacity: capacity,
        };
        self.check_exchange(&exchange)?;
        exchange
            .stream
            .write_all(&(encoded.bytes.len() as u64).to_be_bytes())
            .map_err(|error| error.to_string())?;
        exchange
            .stream
            .write_all(&encoded.bytes)
            .map_err(|error| error.to_string())?;
        Ok(exchange)
    }

    /// Reads only bounded allocated response bytes under the actual current original connection witness.
    fn read<T: serde::de::DeserializeOwned>(&self, exchange: &mut Exchange) -> Result<T, String> {
        self.check_exchange(exchange)?;
        let mut prefix = [0; 8];
        exchange
            .stream
            .read_exact(&mut prefix)
            .map_err(|error| error.to_string())?;
        let length = u64::from_be_bytes(prefix);
        if length == 0 || length > MAX_FRAME_BYTES as u64 {
            return Err("original child reply exceeds its bound".into());
        }
        let _frame = self
            .budget
            .frame(length)
            .map_err(|error| error.to_string())?;
        let mut bytes = vec![0; length as usize];
        exchange
            .stream
            .read_exact(&mut bytes)
            .map_err(|error| error.to_string())?;
        self.check_exchange(exchange)?;
        serde_json::from_slice(&bytes).map_err(|error| error.to_string())
    }

    /// Requires this actual original process and current complete metadata/content quorum.
    pub fn check(&self) -> Result<ControllerProcessIdentity, String> {
        self.process_reply(ChildCommand::Fence)
    }

    /// Checks only actual original process/TLS/epoch/quorum; this never announces complete content ready.
    pub fn check_process(&self) -> Result<ControllerProcessIdentity, String> {
        self.process_reply(ChildCommand::ProcessFence)
    }

    /// Waits only for original PID registration under one startup clock, without any command effects.
    pub fn wait_for_registration(&self) -> Result<(), String> {
        let original = ContentBudget::issue(self.config.budget_ms)?;
        loop {
            original.check()?;
            match self.check_process() {
                Ok(_) => {
                    original.check()?;
                    return Ok(());
                }
                Err(error)
                    if error.contains(
                        "actual original Controller process has no live registered incarnation",
                    ) =>
                {
                    original.check()?;
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(error) => return Err(error),
            }
        }
    }

    /// Verifies one actual original returned process identity under the selected readonly fence scope.
    fn process_reply(&self, command: ChildCommand) -> Result<ControllerProcessIdentity, String> {
        let mut exchange = self.connect(command)?;
        match self.read(&mut exchange)? {
            ChildReply::Fence(process) => {
                crate::supervision::validate_process(&process)?;
                if process.pid != std::process::id()
                    || process.epoch != self.config.epoch
                    || process.instance_id != self.config.instance
                {
                    return Err("actual original child fence names a different incarnation".into());
                }
                Ok(process)
            }
            ChildReply::Rejected(error) => Err(error),
            _ => Err("original child fence reply was invalid".into()),
        }
    }

    /// Reads exact current original semantic/recovery/content predecessor without granting a new effect.
    pub fn view(&self) -> Result<ReplicaView, String> {
        let mut exchange = self.connect(ChildCommand::View)?;
        match self.read(&mut exchange)? {
            ChildReply::View(view) => {
                view.validate(
                    state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
                    &self.config.profile,
                )
                .map_err(|error| error.to_string())?;
                Ok(*view)
            }
            ChildReply::Rejected(error) => Err(error),
            _ => Err("original child complete view reply was invalid".into()),
        }
    }

    /// Observes only the exact original operation; no response can renew or repeat its first work.
    pub fn observe(&self, command: &str) -> Result<Option<ControllerIngressStatus>, String> {
        let mut exchange = self.connect(ChildCommand::Observe {
            command_id: command.into(),
        })?;
        match self.read(&mut exchange)? {
            ChildReply::Original(original) => {
                let record: IngressRecord = serde_json::from_value(original.original.clone())
                    .map_err(|error| error.to_string())?;
                validate_record(&record)?;
                if record.intent.command_id != command || status(&record)? != *original {
                    return Err(
                        "original child observation differs from its retained command".into(),
                    );
                }
                Ok(Some(*original))
            }
            ChildReply::Absent => Ok(None),
            ChildReply::Rejected(error) => Err(error),
            _ => Err("original child operation observation was invalid".into()),
        }
    }

    /// Sends one exact original candidate; source files open only after both durable Ready boundaries.
    pub fn submit(
        &self,
        candidate: ContentApplicationBatch,
        command: &str,
    ) -> Result<ControllerIngressStatus, String> {
        let actual = self
            .source
            .writer_identity()
            .map_err(|error| error.to_string())?;
        if candidate.profile_identity != self.config.profile
            || candidate.epoch != self.config.epoch
            || candidate.source_writer
                != (ApplicationArtifactWriter {
                    owner: actual.owner_id,
                    device: actual.lease_device,
                    inode: actual.lease_inode,
                })
        {
            return Err(
                "complete original candidate differs from the actual owned source writer".into(),
            );
        }
        let mut exchange = self.connect(ChildCommand::Begin {
            command_id: command.into(),
            candidate: Box::new(candidate.clone()),
        })?;
        let ready: ChildReply = self.read(&mut exchange)?;
        let (request_digest, target, process) = match ready {
            ChildReply::Original(original) => {
                return self.validate_original(*original, &candidate, command, None);
            }
            ChildReply::Ready {
                request_digest,
                target,
                process,
            } => (request_digest, *target, process),
            ChildReply::Rejected(error) => return Err(error),
            _ => return Err("original child admission reply was invalid".into()),
        };
        crate::supervision::validate_process(&process)?;
        if process.pid != std::process::id()
            || process.epoch != self.config.epoch
            || process.instance_id != self.config.instance
        {
            return Err("original child Ready names another source process".into());
        }
        let source_origin = crate::content_store::StreamOrigin::Controller {
            process,
            writer: candidate.source_writer.clone(),
            peer_leaf: exchange.evidence.local.local_certificate_digest.clone(),
        };
        target.validate()?;
        if target.profile_identity != self.config.profile
            || target.epoch != self.config.epoch
            || target.batch_digest != candidate.identity()?
            || target.inputs != candidate.inputs
            || target.checkpoint_digest != state::checkpoint_identity(&candidate.batch.checkpoint)
        {
            return Err("original child Ready changed the complete requested candidate".into());
        }
        for input in &target.inputs {
            if input.source_owner != candidate.source_writer.owner {
                continue;
            }
            let receive_id = format!(
                "controller:{:x}",
                Sha256::digest(
                    serde_json::to_vec(&(command, &target, input))
                        .map_err(|error| error.to_string())?
                )
            );
            let notice: crate::transport::Reply = self.read(&mut exchange)?;
            match notice {
                crate::transport::Reply::ContentStream(Ok(
                    crate::transport::StreamNotice::Ready { .. },
                )) => {}
                crate::transport::Reply::ContentStream(Ok(
                    crate::transport::StreamNotice::Original(original),
                )) => {
                    crate::content_store::verify_original_stream_observation(
                        &original,
                        &target,
                        input,
                        &receive_id,
                        self.config.epoch.replica_id,
                        &source_origin,
                    )?;
                    if original.receipt.is_none() {
                        return Err("original input remains Unknown".into());
                    }
                    continue;
                }
                crate::transport::Reply::ContentStream(Err(error)) => return Err(error),
                _ => return Err("original source receive admission was invalid".into()),
            }
            self.check_exchange(&exchange)?;
            let mut source = self
                .source
                .open_replication_source(&input.content_digest, input.byte_size)
                .map_err(|error| error.to_string())?;
            let mut chunk = [0; crate::content_store::STREAM_CHUNK_BYTES];
            let mut offset = 0u64;
            loop {
                self.check_exchange(&exchange)?;
                let count = source.read(&mut chunk).map_err(|error| error.to_string())?;
                self.check_exchange(&exchange)?;
                if count == 0 {
                    break;
                }
                exchange
                    .stream
                    .write_all(&offset.to_be_bytes())
                    .map_err(|error| error.to_string())?;
                exchange
                    .stream
                    .write_all(&(count as u32).to_be_bytes())
                    .map_err(|error| error.to_string())?;
                exchange
                    .stream
                    .write_all(&chunk[..count])
                    .map_err(|error| error.to_string())?;
                offset = offset
                    .checked_add(count as u64)
                    .ok_or("original source offset overflow")?;
            }
            drop(source);
            if offset != input.byte_size {
                return Err("actual original source size changed".into());
            }
            self.check_exchange(&exchange)?;
            exchange
                .stream
                .write_all(&offset.to_be_bytes())
                .map_err(|error| error.to_string())?;
            exchange
                .stream
                .write_all(&0u32.to_be_bytes())
                .map_err(|error| error.to_string())?;
            let complete: crate::transport::Reply = self.read(&mut exchange)?;
            match complete {
                crate::transport::Reply::ContentStreamFinished(Ok(original)) => {
                    crate::content_store::verify_original_stream_observation(
                        &original,
                        &target,
                        input,
                        &receive_id,
                        self.config.epoch.replica_id,
                        &source_origin,
                    )?;
                    if original.receipt.is_none() {
                        return Err("original source byte receipt unavailable".into());
                    }
                }
                crate::transport::Reply::ContentStreamFinished(Err(error)) => return Err(error),
                _ => return Err("original source stream completion reply was invalid".into()),
            }
        }
        match self.read(&mut exchange)? {
            ChildReply::Original(original) => {
                self.validate_original(*original, &candidate, command, Some(&request_digest))
            }
            ChildReply::Rejected(error) => Err(error),
            _ => Err("original complete child receipt was invalid".into()),
        }
    }

    /// Verifies the exact retained first command without converting its nullable result into success.
    fn validate_original(
        &self,
        observed: ControllerIngressStatus,
        candidate: &ContentApplicationBatch,
        command: &str,
        request_digest: Option<&str>,
    ) -> Result<ControllerIngressStatus, String> {
        let original: IngressRecord =
            serde_json::from_value(observed.original.clone()).map_err(|error| error.to_string())?;
        validate_record(&original)?;
        let process = &original.intent.process;
        if status(&original)? != observed
            || original.intent.command_id != command
            || original.intent.candidate.as_ref() != candidate
            || process.pid != std::process::id()
            || process.instance_id != self.config.instance
            || process.epoch != self.config.epoch
            || request_digest.is_some_and(|digest| digest != original.request_digest)
        {
            return Err("original child receipt differs from its complete first request".into());
        }
        Ok(observed)
    }

    /// Confirms only exact original acceptance and current complete quorum; old Unknown remains retained.
    fn confirm(
        &self,
        batch: state::ApplicationCommitBatch,
        recovery: state::ReplicaRecoveryProjection,
    ) -> Result<(), String> {
        let previous = self.view()?;
        let candidate = ContentApplicationBatch::from_source(
            &self.config.profile,
            self.config.epoch,
            &self.source,
            &previous,
            batch,
            recovery,
        )?;
        let command = format!(
            "child:{:x}",
            Sha256::digest(
                serde_json::to_vec(&(&self.config.instance, candidate.identity()?))
                    .map_err(|error| error.to_string())?
            )
        );
        let attempt = self.submit(candidate.clone(), &command);
        let first_failure = attempt
            .as_ref()
            .err()
            .cloned()
            .unwrap_or_else(|| "original operation retained no complete receipt".into());
        if let Ok(observed) = &attempt {
            let record: IngressRecord = serde_json::from_value(observed.original.clone())
                .map_err(|error| error.to_string())?;
            if record.receipt.is_some() {
                self.check_process()?;
                return Ok(());
            }
        }
        let original = self.observe(&command)?.ok_or_else(|| {
            format!(
                "original child command is unavailable after uncertain submission: {:?}",
                first_failure
            )
        })?;
        self.validate_original(original.clone(), &candidate, &command, None)?;
        let retained: IngressRecord =
            serde_json::from_value(original.original).map_err(|error| error.to_string())?;
        let current = self.view()?;
        if current.revision == retained.intent.target.revision
            && current.content.as_ref().is_some_and(|accepted| {
                accepted.batch_digest == retained.intent.target.batch_digest
                    && accepted.candidate.as_ref() == &candidate
            })
        {
            return Ok(());
        }
        Err(format!(
            "original child submission remains Unknown ({first_failure}); readonly evidence grants no repeated effect"
        ))
    }
}

impl state::ApplicationBatchReplicator for ControllerContentClient {
    /// Retains the actual original issuing epoch in application authority evidence.
    fn controller_epoch(&self) -> Option<String> {
        serde_json::to_string(&self.config.epoch).ok()
    }
    /// Returns the exact original startup-frozen application/network profile.
    fn profile_identity(&self) -> &str {
        &self.config.profile
    }
    /// Requires actual owned-process and complete content/quorum authority before SQL mutations.
    fn assert_write_authority(&self) -> Result<(), String> {
        self.check_process().map(|_| ())
    }
    /// Legacy metadata-only confirmation cannot bypass the complete original source contract.
    fn confirm_batch(&self, _batch: state::ApplicationCommitBatch) -> Result<(), String> {
        Err("owned child content requires its complete original recovery projection".into())
    }
    /// Explicitly requests complete original auxiliary authority under the actual source transaction.
    fn requires_recovery_projection(&self) -> bool {
        true
    }
    /// Connects the original source SQL candidate to actual bytes and same-member complete quorum.
    fn confirm_recovery_batch(
        &self,
        batch: state::ApplicationCommitBatch,
        recovery: state::ReplicaRecoveryProjection,
    ) -> Result<(), String> {
        self.confirm(batch, recovery)
    }
}
