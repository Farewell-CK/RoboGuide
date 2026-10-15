//! Durable deployment certificate updates checked after HTTP parsing, before route authority.

use super::*;
use std::sync::MutexGuard;
use std::time::Instant;

/// Per-request transport witness checked inside the same mutex held through application Commit.
pub(super) struct AuthorizedRequestGate {
    /// Actual application writer; this wrapper never releases and reenters it during a transaction.
    inner: Arc<Mutex<()>>,
    /// Current committed grant and Mission ownership authorities.
    controller: Arc<Mutex<ControllerState>>,
    /// Actual TLS certificate or the explicitly configured trusted loopback connection.
    principal: OperatorPrincipal,
    /// Bounded route authorization metadata; no body or credential is retained here.
    request: ControlHttpRequest,
    /// Already selected tenant, checked again against current ownership and delegation.
    tenant: Option<String>,
    /// Original Controller-local clock anchor, never another machine's timestamp.
    received_at: domain::TimestampMs,
    /// Monotonic elapsed time also fences expiry while a blocking worker waits for the writer.
    received_instant: Instant,
}

impl AuthorizedRequestGate {
    /// Captures an immutable transport witness without acquiring authority or copying request bytes.
    pub(super) fn new(
        inner: Arc<Mutex<()>>,
        controller: Arc<Mutex<ControllerState>>,
        principal: OperatorPrincipal,
        request: &ControlHttpRequest,
        tenant: Option<String>,
        received_at: domain::TimestampMs,
    ) -> Self {
        Self {
            inner,
            controller,
            principal,
            request: ControlHttpRequest {
                method: request.method.clone(),
                target: request.target.clone(),
                headers: request
                    .headers
                    .iter()
                    .filter(|(name, _)| {
                        matches!(
                            name.as_str(),
                            "x-roboguide-tenant" | "x-roboguide-operator-role"
                        )
                    })
                    .map(|(name, value)| (name.clone(), value.clone()))
                    .collect(),
                body: Vec::new(),
            },
            tenant,
            received_at,
            received_instant: Instant::now(),
        }
    }

    /// Resolves current exact certificate grants while caller holds the sole application writer.
    fn validate(&self, live: &ControllerState) -> Result<(), String> {
        let principal = &self.principal;
        if let Some(certificate) = &principal.certificate {
            let elapsed =
                u64::try_from(self.received_instant.elapsed().as_millis()).unwrap_or(u64::MAX);
            let now = self.received_at.as_millis().saturating_add(elapsed);
            if now >= certificate.expires_at_ms {
                return Err(
                    "operator transport certificate expired while awaiting authority".into(),
                );
            }
            let (roles, configured) = load_grants(&certificate.path)?;
            let committed = live.bridge.control().operator_authorization();
            if configured.as_ref() != committed {
                return Err(
                    "operator deployment revision changed before transaction; retry admission"
                        .into(),
                );
            }
            let current = roles
                .get(&certificate.digest)
                .ok_or("operator transport leaf is no longer enrolled")?;
            let subject = if let Some(profile) = committed {
                let grant = profile
                    .grants
                    .get(&certificate.digest)
                    .ok_or("operator transport leaf is absent from committed authority")?;
                if !grant.active(now) {
                    return Err(
                        "operator transport grant expired or was revoked before transaction".into(),
                    );
                }
                grant.principal_id.as_str()
            } else {
                certificate.digest.as_str()
            };
            if principal.subject.as_deref() != Some(subject)
                || principal.role.as_deref() != Some(current.role.as_str())
                || principal.tenants.as_ref() != Some(&current.tenant_ids)
            {
                return Err("operator transport delegation changed before transaction".into());
            }
        } else if principal.mtls {
            return Err("operator request has no verified transport witness".into());
        }
        let path = self.request.target.split('?').next().unwrap_or("");
        authorize_operator_request(&self.request, principal, &self.request.method, path)
            .map_err(|(_, error)| error.to_owned())?;
        if authorized_tenant(&self.request, principal)? != self.tenant {
            return Err("operator tenant delegation changed before transaction".into());
        }
        authorize_tenant_path(live, self.tenant.as_deref(), path)
    }
}

impl application::ApplicationWriteGate for AuthorizedRequestGate {
    /// Acquires the real mutex and rechecks authority before exposing the transaction guard.
    fn lock(&self) -> Result<MutexGuard<'_, ()>, String> {
        let guard = self
            .inner
            .lock()
            .map_err(|_| "event-log write gate is poisoned")?;
        let live = self
            .controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?;
        self.validate(&live)?;
        drop(live);
        Ok(guard)
    }
}

/// Actual transport identity, retained independently of a startup role projection.
#[derive(Debug, Clone)]
pub(super) struct OperatorCertificateContext {
    /// Exact peer leaf derived from rustls, never an HTTP header.
    pub(super) digest: String,
    /// Original verified certificate validity fence.
    pub(super) expires_at_ms: u64,
    /// Trusted deployment grant file.
    pub(super) path: std::path::PathBuf,
}

/// Reads bounded versioned grants or an explicit legacy static role mapping.
pub(super) fn load_grants(
    path: &std::path::Path,
) -> Result<
    (
        BTreeMap<String, OperatorGrant>,
        Option<domain::OperatorAuthorization>,
    ),
    String,
> {
    if std::fs::metadata(path)
        .map_err(|error| error.to_string())?
        .len()
        > 2 * 1024 * 1024
    {
        return Err("operator authorization file exceeds its limit".into());
    }
    let value: serde_json::Value =
        serde_json::from_slice(&std::fs::read(path).map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
    if value.get("schema_version").is_some() {
        let profile: domain::OperatorAuthorization =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        profile.validate()?;
        let roles = profile
            .grants
            .iter()
            .map(|(digest, grant)| {
                (
                    digest.clone(),
                    OperatorGrant {
                        role: grant.role.clone(),
                        tenant_ids: grant.tenant_ids.clone(),
                    },
                )
            })
            .collect();
        return Ok((roles, Some(profile)));
    }
    let raw: BTreeMap<String, serde_json::Value> =
        serde_json::from_value(value).map_err(|error| error.to_string())?;
    let roles = raw
        .into_iter()
        .map(|(digest, value)| {
            let grant = if let Some(role) = value.as_str() {
                OperatorGrant {
                    role: role.into(),
                    tenant_ids: BTreeSet::from(["default".into()]),
                }
            } else {
                serde_json::from_value(value).map_err(|error| error.to_string())?
            };
            Ok((digest, grant))
        })
        .collect::<Result<BTreeMap<_, _>, String>>()?;
    let validation = domain::OperatorAuthorization {
        schema_version: "roboguide.operator-authorization/v0.1".into(),
        registry_id: "static-compatibility".into(),
        revision: 1,
        grants: roles
            .iter()
            .map(|(digest, grant)| {
                (
                    digest.clone(),
                    domain::OperatorCertificateGrant {
                        principal_id: digest.clone(),
                        role: grant.role.clone(),
                        tenant_ids: grant.tenant_ids.clone(),
                        not_before_ms: 0,
                        expires_at_ms: 0,
                        revoked: false,
                    },
                )
            })
            .collect(),
    };
    validation.validate()?;
    Ok((roles, None))
}

/// Persists a deployment revision before any newly authorized role or revocation becomes visible.
pub(super) fn synchronize_authority(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &Arc<Mutex<()>>,
    profile: Option<domain::OperatorAuthorization>,
) -> Result<(), String> {
    let _gate = gate
        .lock()
        .map_err(|_| "application write gate is poisoned")?;
    let mut live = controller
        .lock()
        .map_err(|_| "controller lock is poisoned")?;
    if live.bridge.control().operator_authorization() == profile.as_ref() {
        return Ok(());
    }
    let mut candidate = live.clone();
    candidate
        .bridge
        .control_mut()
        .accept_operator_authorization(profile)
        .map_err(|error| error.to_string())?;
    let checkpoint = server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
    log.begin_batch().map_err(|error| error.to_string())?;
    if let Err(error) =
        save_controller_checkpoint(log, &checkpoint).and_then(|()| log.commit_batch())
    {
        let rollback = log.rollback_batch();
        return Err(format!(
            "operator authorization commit failed: {error}; rollback={rollback:?}"
        ));
    }
    *live = candidate;
    Ok(())
}

/// Rechecks actual peer expiry and current committed grant after potentially slow request parsing.
pub(super) fn refresh_principal(
    principal: &OperatorPrincipal,
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &Arc<Mutex<()>>,
    now: domain::TimestampMs,
) -> Result<OperatorPrincipal, String> {
    let Some(certificate) = &principal.certificate else {
        return Ok(principal.clone());
    };
    let (roles, profile) = load_grants(&certificate.path)?;
    synchronize_authority(controller, log, gate, profile.clone())?;
    if now.as_millis() >= certificate.expires_at_ms {
        return Err("operator certificate expired".into());
    }
    let grant = roles
        .get(&certificate.digest)
        .ok_or("operator leaf is not enrolled")?;
    let subject = if let Some(profile) = &profile {
        let current = &profile.grants[&certificate.digest];
        if !current.active(now.as_millis()) {
            return Err("operator certificate is revoked or outside validity".into());
        }
        current.principal_id.clone()
    } else {
        certificate.digest.clone()
    };
    Ok(OperatorPrincipal {
        certificate: Some(certificate.clone()),
        tenants: Some(grant.tenant_ids.clone()),
        subject: Some(subject),
        role: Some(grant.role.clone()),
        mtls: true,
    })
}
