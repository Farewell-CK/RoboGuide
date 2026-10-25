//! Actual authenticated all-member upgrade negotiation; original waits and clocks remain member-local.

use crate::content_store::ContentBudget;
use crate::upgrades::{BinaryWitness, LocalUpgradeBinary, binary_digest};
use crate::*;
use openraft::StoredMembership;
use std::path::{Path, PathBuf};
use tokio::io::AsyncReadExt as _;

/// Closed explicit upgrade transport, refused by old peers before any new authority is installed.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) enum UpgradeWire {
    /// Actual bounded support observation; no drain effects or new grants are created.
    Support {
        /// Original current leader identity and term.
        epoch: ControllerEpoch,
        /// Complete original current voters and configured member bindings.
        membership: StoredMembership<u64, ReplicaMember>,
        /// Exact complete application revision/sequence/checkpoint digest.
        predecessor: AcceptedApplicationIdentity,
        /// Exact locally configured candidate requested, never a sender-provided executable path.
        target: UpgradeBinary,
    },
    /// Original immutable complete intent precedes the member-local application fence.
    Drain {
        /// Complete original first source issue, immutable across retries.
        intent: Box<ApplicationUpgradeIntent>,
        /// Remaining original caller budget, frozen again before this receiver's writer wait.
        budget_ms: u64,
    },
    /// Reconcile original wait only; no new effect budget, stop command or activation.
    ObserveDrain {
        /// Original retained generation operation, never a permission to resend its stop.
        command_id: String,
    },
    /// Observe exact original local input without a new effect budget or stop request.
    ObserveIntent {
        /// Exact original operation whose immutable input is observed.
        command_id: String,
    },
    /// Explicit irreversible abandonment of one original uncertain request, never downgrade.
    Abort {
        /// Current independent infrastructure authority for permission reduction.
        epoch: ControllerEpoch,
        /// Original genuinely retained pending generation operation.
        original_command_id: String,
        /// Original immutable abandonment operation identity.
        command_id: String,
    },
    /// Original authenticated leader executes at most one immutable generation switch.
    Activate {
        /// Original current leader captured before the administrative operation.
        epoch: ControllerEpoch,
        /// Closed original request; actual support and waits are independently collected.
        request: ApplicationUpgradeRequest,
    },
    /// Readonly current committed authority and exact original pending local operations.
    Observe,
}

/// Actual typed responses remain distinct from an application Commit or device-stop certificate.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) enum UpgradeReply {
    /// Actual served support whose leaf is independently checked against the real TLS connection.
    Support(ApplicationUpgradeSupport),
    /// Original local wait is either still uncertain or independently retained.
    Drain(Option<ApplicationUpgradeDrain>),
    /// Original quorum-ordered activation; the application has not yet necessarily migrated its checkpoint.
    Activated(ApplicationUpgradeReceipt),
    /// Original quorum-ordered abandonment leaves every uncertain local wait intact.
    Aborted(ApplicationUpgradeAbort),
    /// Actual original member-local intent; absence is scoped to this original store only.
    Intent(Option<ApplicationUpgradeIntent>),
    /// Independent current view; pending operation IDs never imply no effects.
    Observation {
        /// Actual committed generation authority, absent under original legacy interpretation.
        authority: Option<ApplicationUpgradeAuthority>,
        /// Retained unresolved local operation IDs; absence is scoped to this member only.
        pending: Vec<String>,
    },
}

impl ReplicaNode {
    /// Verifies the actual explicit next executable before offering any upgrade support.
    pub async fn configure_application_upgrade_binary(
        &self,
        path: &Path,
        expected: UpgradeBinary,
    ) -> Result<(), String> {
        expected.validate()?;
        if !path.is_absolute() {
            return Err("upgrade binary requires an explicit absolute path".into());
        }
        let executable = path.to_path_buf();
        let actual_path = executable.clone();
        let (digest, infrastructure, witness) = tokio::task::spawn_blocking(move || {
            let witness = BinaryWitness::capture(&actual_path)?;
            let digest = binary_digest(&actual_path)?;
            witness.require_current(&actual_path)?;
            Ok::<_, String>((
                digest,
                binary_digest(&std::env::current_exe().map_err(|error| error.to_string())?)?,
                witness,
            ))
        })
        .await
        .map_err(|error| error.to_string())??;
        if digest != expected.binary_digest {
            return Err("actual upgrade executable differs from declared bytes".into());
        }
        let mut child = tokio::process::Command::new(&executable)
            .env_clear()
            .arg("--application-schema")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|error| error.to_string())?;
        let mut output = Vec::new();
        let result = tokio::time::timeout(Duration::from_secs(5), async {
            child
                .stdout
                .take()
                .ok_or("actual upgrade schema output is absent")?
                .take(4097)
                .read_to_end(&mut output)
                .await
                .map_err(|error| error.to_string())?;
            child.wait().await.map_err(|error| error.to_string())
        })
        .await;
        let status = match result {
            Ok(value) => value?,
            Err(_) => {
                child.kill().await.map_err(|error| error.to_string())?;
                child.wait().await.map_err(|error| error.to_string())?;
                return Err("actual upgrade executable schema query expired".into());
            }
        };
        if !status.success()
            || output.len() > 4096
            || std::str::from_utf8(&output)
                .map_err(|error| error.to_string())?
                .trim()
                != expected.application_schema
        {
            return Err("actual upgrade executable reports another application schema".into());
        }
        if inspect_upgrade_contract(&executable).await? != "roboguide.application-upgrade/v0.1" {
            return Err(
                "actual target executable does not support explicit generation upgrade".into(),
            );
        }
        let candidate = LocalUpgradeBinary {
            path: executable,
            binary: expected,
            witness,
        };
        candidate.witness.require_current(&candidate.path)?;
        let mut saved = self
            .upgrade_binary
            .write()
            .map_err(|error| error.to_string())?;
        let key = candidate.binary.binary_digest.clone();
        if saved.get(&key).is_some_and(|(original, _)| {
            original.binary != candidate.binary || original.path != candidate.path
        }) || (!saved.contains_key(&key) && saved.len() >= 32)
        {
            return Err(
                "verified upgrade executable cannot be replaced or exceed its bound".into(),
            );
        }
        saved.insert(key, (candidate, infrastructure));
        Ok(())
    }

    /// Returns the exact configured executable only after matching actual committed writer authority.
    pub async fn active_application_upgrade_binary(&self) -> Result<Option<PathBuf>, String> {
        let current = self.store.application_upgrade_authority()?;
        let Some(authority) = current else {
            return Ok(None);
        };
        if authority.receipts.is_empty() {
            return Ok(None);
        }
        let expected = &authority
            .receipts
            .last()
            .ok_or("active upgrade history is empty")?
            .intent
            .request
            .target;
        let (candidate, _) = self
            .upgrade_binary
            .read()
            .map_err(|error| error.to_string())?
            .get(&expected.binary_digest)
            .cloned()
            .ok_or("active writer requires its original configured upgrade executable")?;
        if &candidate.binary != expected {
            return Err(
                "actual configured upgrade executable/policy differs from active authority".into(),
            );
        }
        let path = candidate.path;
        let observed = path.clone();
        tokio::task::spawn_blocking(move || candidate.witness.require_current(&observed))
            .await
            .map_err(|error| error.to_string())??;
        Ok(Some(path))
    }

    /// Reads current committed upgrade state; observation never reissues a stop or renews clocks.
    pub fn application_upgrade_authority(
        &self,
    ) -> Result<Option<ApplicationUpgradeAuthority>, String> {
        self.store.application_upgrade_authority()
    }

    /// Requires exact current consensus and complete application head, independently of application holds.
    fn check_upgrade_predecessor(
        &self,
        epoch: ControllerEpoch,
        membership: &StoredMembership<u64, ReplicaMember>,
        predecessor: &AcceptedApplicationIdentity,
    ) -> Result<(), String> {
        let metrics = self.raft.metrics().borrow().clone();
        let (_, actual) = self.store.try_network_authority()?;
        if metrics.current_term != epoch.term
            || metrics.current_leader != Some(epoch.replica_id)
            || actual != *membership
            || metrics.membership_config.as_ref() != membership
            || membership
                .nodes()
                .map(|(id, _)| *id)
                .collect::<BTreeSet<_>>()
                != self.config.members.keys().copied().collect()
        {
            return Err("original upgrade epoch or complete configured membership changed".into());
        }
        let connection = self
            .store
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        if self
            .store
            .accepted_identity_with_connection(&connection)
            .map_err(|error| error.to_string())?
            != *predecessor
        {
            return Err("original upgrade complete application predecessor changed".into());
        }
        Ok(())
    }

    /// Reads actual startup executable support; no caller boolean can substitute for these local bytes.
    async fn application_upgrade_support(
        &self,
        epoch: ControllerEpoch,
        membership: StoredMembership<u64, ReplicaMember>,
        predecessor: AcceptedApplicationIdentity,
        leaf: String,
        target: UpgradeBinary,
    ) -> Result<ApplicationUpgradeSupport, String> {
        self.check_upgrade_predecessor(epoch, &membership, &predecessor)?;
        let (candidate, infrastructure) = self
            .upgrade_binary
            .read()
            .map_err(|error| error.to_string())?
            .get(&target.binary_digest)
            .cloned()
            .filter(|(candidate, _)| candidate.binary == target)
            .ok_or("member has no independently verified exact configured upgrade executable")?;
        let path = candidate.path;
        tokio::task::spawn_blocking(move || candidate.witness.require_current(&path))
            .await
            .map_err(|error| error.to_string())??;
        self.check_upgrade_predecessor(epoch, &membership, &predecessor)?;
        Ok(ApplicationUpgradeSupport {
            member: self.config.replica_id,
            leaf,
            infrastructure_binary_digest: infrastructure,
            target: candidate.binary,
            membership,
            predecessor,
        })
    }

    /// First local intent fences ingress/spawn before the real supervisor observes and reaps its child.
    async fn receive_application_upgrade_drain(
        &self,
        intent: ApplicationUpgradeIntent,
        clock: ContentBudget,
        leaf: String,
        witness: crate::tls::VerifiedTlsConnection,
    ) -> Result<Option<ApplicationUpgradeDrain>, String> {
        intent.validate()?;
        let support = intent
            .supports
            .iter()
            .find(|proof| proof.member == self.config.replica_id)
            .ok_or("original upgrade lacks this member's actual support")?;
        if support.leaf != leaf {
            return Err("original upgrade support belongs to another served leaf".into());
        }
        self.check_upgrade_predecessor(intent.epoch, &intent.membership, &intent.predecessor)?;
        let local = self
            .application_upgrade_support(
                intent.epoch,
                intent.membership.clone(),
                intent.predecessor.clone(),
                leaf.clone(),
                intent.request.target.clone(),
            )
            .await?;
        if &local != support {
            return Err("actual original upgrade support changed before drain intent".into());
        }
        let observed = self.clone();
        let command = intent.request.command_id.clone();
        tokio::task::spawn_blocking(move || {
            observed.store.admit_application_upgrade(
                intent,
                clock,
                &witness,
                observed.config.replica_id,
                &observed.process_instance,
            )?;
            observed.store.observe_application_upgrade_drain(
                &command,
                observed.config.replica_id,
                &leaf,
                &observed.process_instance,
            )
        })
        .await
        .map_err(|error| error.to_string())?
    }

    /// Freezes actual all-member support before drain effects and commits only the exact original activation.
    pub async fn upgrade_application_generation(
        &self,
        epoch: ControllerEpoch,
        request: ApplicationUpgradeRequest,
    ) -> Result<ApplicationUpgradeReceipt, String> {
        let clock = ContentBudget::issue(request.budget_ms)?;
        self.upgrade_application_generation_checked(epoch, request, clock)
            .await
    }

    /// Uses the original pre-writer receive clock through every member negotiation and activation wait.
    async fn upgrade_application_generation_checked(
        &self,
        epoch: ControllerEpoch,
        request: ApplicationUpgradeRequest,
        clock: ContentBudget,
    ) -> Result<ApplicationUpgradeReceipt, String> {
        let _serialization = self
            .leader_transfer_writer
            .try_lock()
            .map_err(|_| "another infrastructure transition is active")?;
        self.check_infrastructure_epoch(epoch).await?;
        if let Some(authority) = self.store.application_upgrade_authority()?
            && let Some(original) = authority
                .receipts
                .iter()
                .find(|receipt| receipt.intent.request.command_id == request.command_id)
        {
            if original.intent.request != request {
                return Err("original upgrade ID was reused with another request".into());
            }
            return Ok(original.clone());
        }
        let previous = self.store.local_application_upgrade(&request.command_id)?;
        let (intent, original_clock) = if let Some(original) = previous {
            if original.intent.request != request || original.intent.epoch != epoch {
                return Err(
                    "original upgrade request/epoch differs; readonly reconciliation is required"
                        .into(),
                );
            }
            original.clock.check()?;
            (original.intent, original.clock)
        } else {
            request.target.validate()?;
            let view = self.accepted_view()?;
            let revision = view
                .application_upgrade
                .as_ref()
                .map_or(0, |authority| authority.receipts.len() as u64);
            let active = view.application_upgrade.as_ref().map_or(
                self.config.application_schema.as_str(),
                ApplicationUpgradeAuthority::active_schema,
            );
            if request.expected_revision != revision || request.from_schema != active {
                return Err("application upgrade original writer/revision differs".into());
            }
            self.store.require_new_application_binary(
                &request.target,
                view.application_upgrade.as_ref(),
            )?;
            let (_, membership) = self.store.try_network_authority()?;
            let predecessor = {
                let sql = self
                    .store
                    .connection
                    .lock()
                    .map_err(|error| error.to_string())?;
                self.store
                    .accepted_identity_with_connection(&sql)
                    .map_err(|error| error.to_string())?
            };
            let mut supports = Vec::new();
            for member in self.config.members.keys() {
                clock.check()?;
                let reply = crate::transport::upgrade_exchange(
                    self,
                    *member,
                    UpgradeWire::Support {
                        epoch,
                        membership: membership.clone(),
                        predecessor: predecessor.clone(),
                        target: request.target.clone(),
                    },
                    clock.remaining_ms()?,
                )
                .await?;
                let UpgradeReply::Support(proof) = reply else {
                    return Err("member did not return actual upgrade support".into());
                };
                if proof.member != *member || proof.target != request.target {
                    return Err(
                        "actual member supports another target generation/binary/policy".into(),
                    );
                }
                supports.push(proof);
            }
            let intent = ApplicationUpgradeIntent {
                request,
                epoch,
                membership,
                predecessor,
                supports,
            };
            intent.validate()?;
            self.check_infrastructure_epoch(epoch).await?;
            self.check_upgrade_predecessor(epoch, &intent.membership, &intent.predecessor)?;
            let local = self.config.replica_id;
            let _ = crate::transport::upgrade_exchange(
                self,
                local,
                UpgradeWire::Drain {
                    intent: Box::new(intent.clone()),
                    budget_ms: clock.remaining_ms()?,
                },
                clock.remaining_ms()?,
            )
            .await?;
            let original = self
                .store
                .local_application_upgrade(&intent.request.command_id)?
                .ok_or("original source upgrade intent was not durably admitted")?;
            (intent, original.clock)
        };
        let mut drains = Vec::new();
        for member in self.config.members.keys() {
            original_clock.check()?;
            let mut reply = crate::transport::upgrade_exchange(
                self,
                *member,
                UpgradeWire::Drain {
                    intent: Box::new(intent.clone()),
                    budget_ms: original_clock.remaining_ms()?,
                },
                original_clock.remaining_ms()?,
            )
            .await?;
            loop {
                match reply {
                    UpgradeReply::Drain(Some(drain)) => {
                        drains.push(drain);
                        break;
                    }
                    UpgradeReply::Drain(None) => {}
                    _ => {
                        return Err(
                            "original upgrade member returned another drain interpretation".into(),
                        );
                    }
                }
                original_clock.check()?;
                tokio::time::sleep(Duration::from_millis(30)).await;
                reply = crate::transport::upgrade_exchange(
                    self,
                    *member,
                    UpgradeWire::ObserveDrain {
                        command_id: intent.request.command_id.clone(),
                    },
                    original_clock.remaining_ms()?,
                )
                .await?;
            }
        }
        self.check_infrastructure_epoch(epoch).await?;
        self.check_upgrade_predecessor(epoch, &intent.membership, &intent.predecessor)?;
        original_clock.check()?;
        let receipt = ApplicationUpgradeReceipt {
            revision: intent.request.expected_revision + 1,
            intent,
            drains,
        };
        let mut authority =
            self.store
                .application_upgrade_authority()?
                .unwrap_or(ApplicationUpgradeAuthority {
                    schema: "roboguide.application-upgrade-authority/v0.1".into(),
                    original_schema: self.config.application_schema.clone(),
                    receipts: Vec::new(),
                    aborts: Vec::new(),
                });
        authority.receipts.push(receipt.clone());
        authority.validate(&self.config.application_schema)?;
        let remaining = original_clock.remaining_ms()?;
        let result = tokio::time::timeout(
            Duration::from_millis(remaining),
            self.raft.client_write(ReplicaCommand::ApplicationUpgrade {
                application_upgrade: Box::new(receipt.clone()),
            }),
        )
        .await
        .map_err(|_| "original upgrade activation outcome is uncertain after deadline")?
        .map_err(|error| error.to_string())?;
        if !result.data.accepted {
            return Err(result
                .data
                .rejection
                .unwrap_or("original upgrade activation was refused".into()));
        }
        Ok(receipt)
    }

    /// Reconciles actual original local wait receipts after restart/abandonment without repeating a stop.
    pub async fn reconcile_application_upgrade_drains(&self) -> Result<(), String> {
        let observed = self.clone();
        tokio::task::spawn_blocking(move || {
            for command in observed.store.pending_application_upgrade_ids()? {
                let original = observed
                    .store
                    .local_application_upgrade(&command)?
                    .ok_or("original local upgrade disappeared")?;
                let leaf = original
                    .intent
                    .supports
                    .iter()
                    .find(|proof| proof.member == observed.config.replica_id)
                    .ok_or("original local support disappeared")?
                    .leaf
                    .clone();
                observed.store.observe_application_upgrade_drain(
                    &command,
                    observed.config.replica_id,
                    &leaf,
                    &observed.process_instance,
                )?;
            }
            Ok::<_, String>(())
        })
        .await
        .map_err(|error| error.to_string())?
    }

    /// Abandons a genuinely observed original operation under a fresh quorum, preserving all wait fences.
    pub async fn abort_application_generation(
        &self,
        epoch: ControllerEpoch,
        original_command_id: String,
        command_id: String,
    ) -> Result<ApplicationUpgradeAbort, String> {
        let _serialization = self
            .leader_transfer_writer
            .try_lock()
            .map_err(|_| "another infrastructure transition is active")?;
        self.check_infrastructure_epoch(epoch).await?;
        if let Some(authority) = self.store.application_upgrade_authority()? {
            if authority
                .receipts
                .iter()
                .any(|receipt| receipt.intent.request.command_id == original_command_id)
            {
                return Err(
                    "activated application generation cannot be abandoned or downgraded".into(),
                );
            }
            if let Some(abort) = authority.aborts.iter().find(|abort| {
                abort.intent.request.command_id == original_command_id
                    || abort.command_id == command_id
            }) {
                if abort.intent.request.command_id != original_command_id
                    || abort.command_id != command_id
                {
                    return Err("original abandonment identity differs".into());
                }
                return Ok(abort.clone());
            }
        }
        let mut original: Option<ApplicationUpgradeIntent> = None;
        for member in self.config.members.keys() {
            let reply = crate::transport::upgrade_exchange(
                self,
                *member,
                UpgradeWire::ObserveIntent {
                    command_id: original_command_id.clone(),
                },
                5000,
            )
            .await?;
            if let UpgradeReply::Intent(Some(intent)) = reply {
                intent.validate()?;
                if original.as_ref().is_some_and(|saved| saved != &intent) {
                    return Err(
                        "members retain different original application upgrade intents".into(),
                    );
                }
                original = Some(intent);
            } else if !matches!(reply, UpgradeReply::Intent(None)) {
                return Err("member did not return original local intent observation".into());
            }
        }
        let intent = original.ok_or("no member retains the original upgrade operation")?;
        let (_, membership) = self.store.try_network_authority()?;
        if membership != intent.membership {
            return Err(
                "original upgrade abandonment requires unchanged complete membership".into(),
            );
        }
        self.check_infrastructure_epoch(epoch).await?;
        let abort = ApplicationUpgradeAbort {
            command_id,
            intent,
            epoch,
            membership,
        };
        let result = tokio::time::timeout(
            Duration::from_secs(5),
            self.raft
                .client_write(ReplicaCommand::ApplicationUpgradeAbort {
                    application_upgrade_abort: Box::new(abort.clone()),
                }),
        )
        .await
        .map_err(|_| "original application upgrade abandonment outcome is uncertain")?
        .map_err(|error| error.to_string())?;
        if !result.data.accepted {
            return Err(result
                .data
                .rejection
                .unwrap_or("original abandonment was refused".into()));
        }
        Ok(abort)
    }
}

/// Independently queries the actual target's explicit protocol with bounded output, time and real wait.
async fn inspect_upgrade_contract(path: &Path) -> Result<String, String> {
    let mut child = tokio::process::Command::new(path)
        .env_clear()
        .arg("--application-upgrade-contract")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .map_err(|error| error.to_string())?;
    let mut bytes = Vec::new();
    let result = tokio::time::timeout(Duration::from_secs(5), async {
        child
            .stdout
            .take()
            .ok_or("actual contract pipe is absent")?
            .take(4097)
            .read_to_end(&mut bytes)
            .await
            .map_err(|error| error.to_string())?;
        child.wait().await.map_err(|error| error.to_string())
    })
    .await;
    let status = match result {
        Ok(value) => value?,
        Err(_) => {
            child.kill().await.map_err(|error| error.to_string())?;
            child.wait().await.map_err(|error| error.to_string())?;
            return Err("actual application upgrade contract query expired".into());
        }
    };
    if !status.success() || bytes.len() > 4096 {
        return Err(
            "actual application upgrade contract query failed or exceeded its bound".into(),
        );
    }
    Ok(std::str::from_utf8(&bytes)
        .map_err(|error| error.to_string())?
        .trim()
        .into())
}

/// Handles one actual authenticated connection; serialized observations cannot grant fresh authority.
pub(crate) async fn handle(
    node: &ReplicaNode,
    sender: u64,
    connection: &crate::tls::VerifiedTlsConnection,
    wire: UpgradeWire,
    clock: Option<ContentBudget>,
) -> Result<UpgradeReply, String> {
    if !connection.authorize(sender, node.config.replica_id)? {
        return Err("actual upgrade peer has no current permission".into());
    }
    let leaf = connection.local.local_certificate_digest.clone();
    match wire {
        UpgradeWire::Support {
            epoch,
            membership,
            predecessor,
            target,
        } => {
            if sender != epoch.replica_id {
                return Err("upgrade support requires its original leader connection".into());
            }
            node.application_upgrade_support(epoch, membership, predecessor, leaf, target)
                .await
                .map(UpgradeReply::Support)
        }
        UpgradeWire::Drain {
            intent,
            budget_ms: _,
        } => {
            let clock = clock.ok_or("original upgrade receive clock is absent")?;
            if sender != intent.epoch.replica_id {
                return Err("upgrade drain requires its original leader connection".into());
            }
            node.receive_application_upgrade_drain(*intent, clock, leaf, connection.clone())
                .await
                .map(UpgradeReply::Drain)
        }
        UpgradeWire::ObserveDrain { command_id } => {
            let observed = node.clone();
            tokio::task::spawn_blocking(move || {
                let original = observed
                    .store
                    .local_application_upgrade(&command_id)?
                    .ok_or("original local upgrade is absent")?;
                let original_leaf = original
                    .intent
                    .supports
                    .iter()
                    .find(|proof| proof.member == observed.config.replica_id)
                    .ok_or("original local support is absent")?
                    .leaf
                    .clone();
                observed.store.observe_application_upgrade_drain(
                    &command_id,
                    observed.config.replica_id,
                    &original_leaf,
                    &observed.process_instance,
                )
            })
            .await
            .map_err(|error| error.to_string())?
            .map(UpgradeReply::Drain)
        }
        UpgradeWire::ObserveIntent { command_id } => node
            .store
            .local_application_upgrade(&command_id)
            .map(|operation| UpgradeReply::Intent(operation.map(|value| value.intent))),
        UpgradeWire::Abort {
            epoch,
            original_command_id,
            command_id,
        } => node
            .abort_application_generation(epoch, original_command_id, command_id)
            .await
            .map(UpgradeReply::Aborted),
        UpgradeWire::Activate { epoch, request } => node
            .upgrade_application_generation_checked(
                epoch,
                request,
                clock.ok_or("original administration receive clock is absent")?,
            )
            .await
            .map(UpgradeReply::Activated),
        UpgradeWire::Observe => Ok(UpgradeReply::Observation {
            authority: node.store.application_upgrade_authority()?,
            pending: node.store.pending_application_upgrade_ids()?,
        }),
    }
}
