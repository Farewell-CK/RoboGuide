//! Durable deployment certificate updates checked after HTTP parsing, before route authority.

use super::*;

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
