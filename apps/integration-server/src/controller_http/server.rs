//! Mission and operator HTTP request routing.

use super::protocol::{
    parse_query, read_control_http_request, write_http_response, write_text_response,
};
use super::view::{
    inventory_json, memory_providers_json, relation_kind_name, state_providers_json,
    state_records_json_with_tenant,
};
use crate::application::ApplicationWriteGate;
use crate::diagnostics::StageObservation;
use crate::*;
use ports::SharedNodeStateReader;
use ports::{DiagnosticHandle, DiagnosticKind, DiagnosticStage};
use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use sha2::{Digest as _, Sha256};
use std::collections::BTreeMap;
use std::io::BufReader;
use std::sync::Arc;
use tokio_rustls::TlsAcceptor;

#[path = "operator_authorization.rs"]
mod authorization;
use authorization::{
    AuthorizedRequestGate, OperatorCertificateContext, load_grants, refresh_principal,
    synchronize_authority,
};

#[cfg(test)]
#[path = "operator_tests.rs"]
mod operator_tests;

/// Operator role permitted to acknowledge a crash-loop quarantine.
const RECOVERY_ADMIN_ROLE: &str = "recovery-admin";

/// Authenticated operator context attached to one HTTP connection.
#[derive(Debug, Clone, Default)]
struct OperatorPrincipal {
    /// Original TLS leaf and trusted deployment path, if this is a real mTLS connection.
    certificate: Option<OperatorCertificateContext>,
    /// Certificate-configured tenant delegation; None is the explicit trusted local/test profile.
    tenants: Option<BTreeSet<String>>,
    /// Certificate-derived audit subject; absent only in explicitly trusted local mode.
    subject: Option<String>,
    /// Role selected solely by a certificate map or explicit loopback deployment configuration.
    role: Option<String>,
    /// Whether the connection completed the configured mutual-TLS handshake.
    mtls: bool,
}

/// Validates an optional role hint against the connection principal; headers never grant authority.
fn operator_role(request: &ControlHttpRequest, principal: &OperatorPrincipal) -> Option<String> {
    let header = request.headers.get("x-roboguide-operator-role").cloned();
    if header.as_deref().is_some_and(|role| {
        !matches!(
            role,
            "viewer" | "operator" | "recovery-admin" | "metrics-reader"
        )
    }) {
        return None;
    }
    let role = principal
        .role
        .clone()
        .or_else(|| (!principal.mtls).then(|| "operator".into()));
    match (role, header) {
        (Some(role), None) => Some(role),
        (Some(role), Some(header)) if role == header => Some(role),
        _ => None,
    }
}

/// Resolves a tenant selector only within certificate-configured delegation; headers never grant scope.
fn authorized_tenant(
    request: &ControlHttpRequest,
    principal: &OperatorPrincipal,
) -> Result<Option<String>, String> {
    let requested = request.headers.get("x-roboguide-tenant").cloned();
    if requested
        .as_ref()
        .is_some_and(|tenant| tenant.trim().is_empty() || tenant.len() > 128)
    {
        return Err("tenant selector is invalid".into());
    }
    let Some(allowed) = &principal.tenants else {
        if requested.as_deref() == Some("local") {
            return Ok(None);
        }
        return Ok(requested);
    };
    if allowed.contains("*") {
        return Ok(requested);
    }
    let tenant = requested
        .or_else(|| {
            (allowed.len() == 1).then(|| allowed.iter().next().expect("one tenant").clone())
        })
        .ok_or("certificate delegation requires one explicit tenant selector")?;
    if !allowed.contains(&tenant) {
        return Err("certificate does not authorize this tenant".into());
    }
    Ok(Some(tenant))
}

/// Enforces persisted tenant ownership before any read, cancel, resume, recovery or mutation.
fn authorize_tenant_path(
    controller: &ControllerState,
    tenant: Option<&str>,
    path: &str,
) -> Result<(), String> {
    let Some(tenant) = tenant else {
        return Ok(());
    };
    if path == "/metrics" {
        return Err("global metrics require global delegation".into());
    }
    if path.starts_with("/v1/")
        && !matches!(
            path,
            "/v1/missions"
                | "/v1/inventory"
                | "/v1/node-identities"
                | "/v1/verifier-evidence"
                | "/v1/state/records"
                | "/v1/state/providers"
                | "/v1/memory/use-queries"
                | "/v1/memory/handoffs"
        )
        && !path.starts_with("/v1/missions/")
        && !path.starts_with("/v1/executions/")
        && !path.starts_with("/v1/groups/")
        && !path.starts_with("/v1/memory/handoffs/")
    {
        return Err("global projection requires explicit global delegation".into());
    }
    let scope = if let Some(suffix) = path.strip_prefix("/v1/missions/") {
        if matches!(suffix, "assess-initial-support" | "validate-plan") {
            return Ok(());
        }
        let id = suffix
            .split('/')
            .next()
            .ok_or("Mission path has no identity")?;
        domain::MissionId::new(id).ok()
    } else if let Some(suffix) = path.strip_prefix("/v1/executions/") {
        let id = suffix
            .split('/')
            .next()
            .ok_or("execution path has no identity")?;
        controller
            .bridge
            .attempt_history()
            .into_iter()
            .find(|attempt| attempt.execution_id() == id)
            .map(|attempt| attempt.command().mission_id().clone())
    } else if let Some(suffix) = path.strip_prefix("/v1/groups/") {
        let id = suffix
            .split('/')
            .next()
            .ok_or("Group path has no identity")?;
        controller
            .orchestrator
            .mission_ids()
            .into_iter()
            .find(|mission| {
                controller
                    .orchestrator
                    .execution(mission)
                    .is_some_and(|execution| execution.group_id().as_str() == id)
            })
    } else {
        None
    };
    if let Some(mission) = scope
        && controller.orchestrator.execution(&mission).is_some()
        && controller.bridge.control().mission_tenant(&mission) != Some(tenant)
    {
        return Err("Mission belongs to another tenant or lacks trusted migration".into());
    }
    if matches!(
        path,
        "/v1/events"
            | "/v1/recovery"
            | "/v1/execution-attempts"
            | "/v1/scheduling-reservations"
            | "/v1/maintenance/archive"
            | "/v1/memory/imports"
            | "/v1/memory/import-queries"
            | "/v1/attestation/challenges"
            | "/v1/attestation/evidence"
            | "/v1/node-identities/commands"
            | "/v1/tenant-migrations"
            | "/v1/state-policies"
            | "/v1/verifier-sources"
            | "/v1/belief-policies"
            | "/v1/memory-authorizations"
            | "/v1/artifact-authorizations"
    ) {
        return Err(
            "global administration/diagnostics require an explicitly delegated global principal"
                .into(),
        );
    }
    Ok(())
}

/// Central fail-closed route authorization; the immutable Mission contract remains independently checked.
fn authorize_operator_request(
    request: &ControlHttpRequest,
    principal: &OperatorPrincipal,
    method: &str,
    path: &str,
) -> Result<(), (&'static str, &'static str)> {
    if operator_role(request, principal).as_deref() == Some("metrics-reader") {
        return if method == "GET" && matches!(path, "/metrics" | "/healthz" | "/readyz") {
            Ok(())
        } else {
            Err((
                "403 Forbidden",
                "metrics-only principal cannot access this route",
            ))
        };
    }
    let required = match (method, path) {
        (
            "GET",
            "/v1/state-policies"
            | "/v1/verifier-sources"
            | "/v1/belief-policies"
            | "/v1/memory-authorizations"
            | "/v1/artifact-authorizations",
        ) => 2,
        ("GET", _)
        | ("POST", "/v1/missions/assess-initial-support" | "/v1/missions/validate-plan") => 0,
        ("POST", "/v1/missions") => 1,
        ("POST", "/v1/maintenance/archive") => 2,
        ("POST", "/v1/authority/recover") => 2,
        ("POST", "/v1/memory/imports") => 2,
        ("POST", "/v1/memory/import-queries") => 2,
        ("POST", "/v1/memory/use-queries") => 2,
        ("POST", "/v1/memory/handoffs") => 2,
        ("POST", path) if path.starts_with("/v1/memory/handoffs/") && path.ends_with("/commit") => {
            2
        }
        ("POST", "/v1/node-identities/commands") => 2,
        ("POST", "/v1/tenant-migrations") => 2,
        (
            "POST",
            "/v1/state-policies"
            | "/v1/verifier-sources"
            | "/v1/belief-policies"
            | "/v1/memory-authorizations",
        ) => 2,
        ("POST", "/v1/artifact-authorizations") => 2,
        ("POST", "/v1/verifier-evidence") => 1,
        ("POST", "/v1/attestation/challenges" | "/v1/attestation/evidence") => 2,
        ("POST", path)
            if path.starts_with("/v1/missions/")
                && [
                    "/cancel",
                    "/scheduling-policy",
                    "/scheduling-preemption",
                    "/resume",
                ]
                .iter()
                .any(|suffix| path.ends_with(suffix)) =>
        {
            if path.ends_with("/resume")
                && serde_json::from_slice::<serde_json::Value>(&request.body)
                    .ok()
                    .and_then(|body| {
                        body.get("acknowledge_crash_loop")
                            .and_then(serde_json::Value::as_bool)
                    })
                    == Some(true)
            {
                2
            } else {
                1
            }
        }
        ("POST", path) if path.starts_with("/v1/missions/") && path.ends_with("/review") => 2,
        ("POST", path) if path.starts_with("/v1/groups/") && path.ends_with("/recover") => 2,
        ("POST", path) if path.starts_with("/v1/executions/") && path.ends_with("/recover") => 2,
        ("POST", path) if path.starts_with("/v1/executions/") && path.ends_with("/cancel") => 1,
        _ => return Err(("404 Not Found", "operator route is not supported")),
    };
    let role = operator_role(request, principal)
        .ok_or(("401 Unauthorized", "operator role is not authenticated"))?;
    let granted = match role.as_str() {
        "viewer" => 0,
        "operator" => 1,
        "recovery-admin" => 2,
        _ => return Err(("401 Unauthorized", "invalid operator principal")),
    };
    if granted < required {
        Err((
            "403 Forbidden",
            "operator role does not authorize this operation",
        ))
    } else {
        Ok(())
    }
}

/// Freezes an explicit development-only administrator context; remote and mTLS listeners cannot use it.
fn local_operator_principal(
    address: std::net::SocketAddr,
    mtls: bool,
) -> Result<OperatorPrincipal, String> {
    let enabled = match std::env::var("ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN").as_deref() {
        Ok("1") => true,
        Ok("0") | Err(std::env::VarError::NotPresent) => false,
        _ => return Err("local recovery administrator flag must be 0 or 1".into()),
    };
    if enabled && (!address.ip().is_loopback() || mtls) {
        return Err(
            "local recovery administrator is an explicit loopback-only development profile".into(),
        );
    }
    Ok(OperatorPrincipal {
        certificate: None,
        tenants: None,
        subject: None,
        role: Some(
            if enabled {
                RECOVERY_ADMIN_ROLE
            } else {
                "operator"
            }
            .into(),
        ),
        mtls: false,
    })
}

/// Loaded mTLS listener and certificate-to-role policy for operator HTTP.
#[derive(Clone)]
struct OperatorTlsProfile {
    /// Rustls acceptor requiring a client certificate signed by the configured CA.
    acceptor: TlsAcceptor,
    /// Digest-to-role mapping; the wire certificate never grants a role by itself.
    role_map_path: std::path::PathBuf,
}

/// Deployment-only grant; request headers may select within this scope but cannot grant it.
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct OperatorGrant {
    /// Authorized role.
    role: String,
    /// Explicit tenant delegation; global wildcard requires recovery administrator authority.
    tenant_ids: BTreeSet<String>,
}

impl OperatorTlsProfile {
    /// Derives one bounded operator role from the peer's leaf certificate digest.
    fn principal<S>(
        &self,
        stream: &tokio_rustls::server::TlsStream<S>,
    ) -> Result<OperatorPrincipal, String> {
        let certificates = stream
            .get_ref()
            .1
            .peer_certificates()
            .ok_or("operator mTLS peer certificate is missing")?;
        let certificate = certificates
            .first()
            .ok_or("operator mTLS peer certificate chain is empty")?;
        let digest = format!("sha256:{:x}", Sha256::digest(certificate.as_ref()));
        let (roles, _) = load_grants(&self.role_map_path)?;
        let role = roles
            .get(&digest)
            .cloned()
            .ok_or("operator certificate is not authorized")?;
        Ok(OperatorPrincipal {
            certificate: Some(OperatorCertificateContext {
                expires_at_ms: integration::verified_certificate_expires_at_ms(
                    certificate.as_ref(),
                )
                .map_err(|error| error.to_string())?,
                digest: digest.clone(),
                path: self.role_map_path.clone(),
            }),
            tenants: Some(role.tenant_ids),
            subject: Some(digest),
            role: Some(role.role),
            mtls: true,
        })
    }
}

/// Loads a bounded PEM certificate chain for the operator listener.
fn operator_certificates(path: &std::path::Path) -> Result<Vec<CertificateDer<'static>>, String> {
    let mut reader = BufReader::new(std::fs::File::open(path).map_err(|error| error.to_string())?);
    rustls_pemfile::certs(&mut reader)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("operator certificate parse failed: {error}"))
}

/// Loads the first supported PEM private key for the operator listener.
fn operator_private_key(path: &std::path::Path) -> Result<PrivateKeyDer<'static>, String> {
    let mut reader = BufReader::new(std::fs::File::open(path).map_err(|error| error.to_string())?);
    rustls_pemfile::private_key(&mut reader)
        .map_err(|error| format!("operator key parse failed: {error}"))?
        .ok_or_else(|| "operator private key is empty".into())
}

/// Resolves optional operator mTLS and certificate-role configuration once at startup.
fn configured_operator_tls()
-> Result<Option<OperatorTlsProfile>, Box<dyn std::error::Error + Send + Sync>> {
    let names = [
        "ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT",
        "ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY",
        "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA",
        "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP",
    ];
    let values = names.iter().map(std::env::var_os).collect::<Vec<_>>();
    if values.iter().all(Option::is_none) {
        return Ok(None);
    }
    if values.iter().any(Option::is_none) {
        return Err(
            "operator mTLS requires server cert, server key, client CA and role map".into(),
        );
    }
    load_operator_tls(
        std::path::Path::new(values[0].as_ref().expect("validated path")),
        std::path::Path::new(values[1].as_ref().expect("validated path")),
        std::path::Path::new(values[2].as_ref().expect("validated path")),
        std::path::Path::new(values[3].as_ref().expect("validated role map")),
    )
    .map(Some)
}

/// Builds the same production mTLS policy from explicit files, without process-global test configuration.
fn load_operator_tls(
    certificate_path: &std::path::Path,
    key_path: &std::path::Path,
    ca_path: &std::path::Path,
    role_map_path: &std::path::Path,
) -> Result<OperatorTlsProfile, Box<dyn std::error::Error + Send + Sync>> {
    let server_cert = operator_certificates(certificate_path)?;
    let server_key = operator_private_key(key_path)?;
    let ca = operator_certificates(ca_path)?;
    let mut roots = rustls::RootCertStore::empty();
    for certificate in ca {
        roots.add(certificate)?;
    }
    load_grants(role_map_path)?;
    let _ = rustls::crypto::ring::default_provider().install_default();
    let verifier = rustls::server::WebPkiClientVerifier::builder(Arc::new(roots)).build()?;
    let server = rustls::ServerConfig::builder()
        .with_client_cert_verifier(verifier)
        .with_single_cert(server_cert, server_key)?;
    Ok(OperatorTlsProfile {
        acceptor: TlsAcceptor::from(Arc::new(server)),
        role_map_path: role_map_path.into(),
    })
}
/// Serves the local Phase 1 Mission and operator diagnostics API.
pub(crate) async fn serve_http(
    address: std::net::SocketAddr,
    controller: Arc<Mutex<ControllerState>>,
    event_log: state::SqliteEventLog,
    event_write_gate: Arc<Mutex<()>>,
    clock: Arc<runtime::SystemMonotonicClock>,
    deployment_feasibility: Option<Arc<DeploymentFeasibility>>,
    verifier_feed: Option<Arc<TaskVerifierFeed>>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let limit = super::network::parse_connection_limit(
        std::env::var("ROBOGUIDE_OPERATOR_CONNECTION_LIMIT")
            .ok()
            .as_deref(),
    )?;
    let operator_tls = application::run_synchronous_transaction(configured_operator_tls)?;
    application::run_synchronous_transaction(|| {
        let profile = operator_tls
            .as_ref()
            .map(|tls| load_grants(&tls.role_map_path).map(|(_, profile)| profile))
            .transpose()?
            .flatten();
        synchronize_authority(&controller, &event_log, &event_write_gate, profile)
    })?;
    let diagnostics = application::run_synchronous_transaction(|| {
        controller
            .lock()
            .map(|live| live.bridge.diagnostics().clone())
            .map_err(|_| "controller lock is poisoned")
    })?;
    let permits = Arc::new(tokio::sync::Semaphore::new(limit));
    let mut rejected = 0_u64;
    let mut sampling = tokio::time::interval(std::time::Duration::from_secs(1));
    sampling.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let listener = tokio::net::TcpListener::bind(address).await?;
    let local_principal = local_operator_principal(address, operator_tls.is_some())?;
    loop {
        let (mut stream, _) = tokio::select! {
            accepted = listener.accept() => accepted?,
            _ = sampling.tick() => {
                super::network::observe_connections(&diagnostics, &permits, limit, rejected);
                continue;
            }
        };
        let Ok(permit) = permits.clone().try_acquire_owned() else {
            rejected = rejected.saturating_add(1);
            super::network::observe_connections(&diagnostics, &permits, limit, rejected);
            drop(stream);
            continue;
        };
        let shared_controller = controller.clone();
        let log = event_log.clone();
        let write_gate = event_write_gate.clone();
        let shared_clock = clock.clone();
        let shared_feasibility = deployment_feasibility.clone();
        let shared_verifier_feed = verifier_feed.clone();
        let operator_tls = operator_tls.clone();
        let local_principal = local_principal.clone();
        tokio::spawn(async move {
            let _permit = permit;
            let result = if let Some(profile) = operator_tls {
                match tokio::time::timeout(
                    CONTROL_HTTP_REQUEST_TIMEOUT,
                    profile.acceptor.accept(stream),
                )
                .await
                {
                    Ok(Ok(mut tls_stream)) => {
                        let principal = match application::run_synchronous_transaction(|| {
                            profile.principal(&tls_stream)
                        }) {
                            Ok(principal) => principal,
                            Err(error) => {
                                eprintln!("operator mTLS identity rejected: {error}");
                                return;
                            }
                        };
                        handle_http_connection_with_principal(
                            &mut tls_stream,
                            &shared_controller,
                            &log,
                            &write_gate,
                            shared_clock.as_ref(),
                            shared_feasibility.as_deref(),
                            shared_verifier_feed.as_deref(),
                            &principal,
                        )
                        .await
                    }
                    Ok(Err(error)) => {
                        Err(format!("operator mTLS handshake failed: {error:?}").into())
                    }
                    Err(_) => Err("operator mTLS handshake timed out".into()),
                }
            } else {
                handle_http_connection_with_principal(
                    &mut stream,
                    &shared_controller,
                    &log,
                    &write_gate,
                    shared_clock.as_ref(),
                    shared_feasibility.as_deref(),
                    shared_verifier_feed.as_deref(),
                    &local_principal,
                )
                .await
            };
            if let Err(error) = result {
                eprintln!("control HTTP request failed: {error}");
            }
        });
    }
}

/// Handles one bounded HTTP/1.1 request for Mission commands and diagnostics.
#[cfg(test)]
pub(crate) async fn handle_http_connection<
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
>(
    stream: &mut S,
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &Arc<Mutex<()>>,
    clock: &(impl Clock + Sync),
    deployment_feasibility: Option<&DeploymentFeasibility>,
    verifier_feed: Option<&TaskVerifierFeed>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    handle_http_connection_with_principal(
        stream,
        controller,
        event_log,
        event_write_gate,
        clock,
        deployment_feasibility,
        verifier_feed,
        &OperatorPrincipal::default(),
    )
    .await
}

/// Supplies an explicitly trusted local administrator to isolated application fixtures.
#[cfg(test)]
pub(crate) async fn handle_recovery_admin_test_connection<
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
>(
    stream: &mut S,
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &Arc<Mutex<()>>,
    clock: &(impl Clock + Sync),
    deployment_feasibility: Option<&DeploymentFeasibility>,
    verifier_feed: Option<&TaskVerifierFeed>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    handle_http_connection_with_principal(
        stream,
        controller,
        event_log,
        event_write_gate,
        clock,
        deployment_feasibility,
        verifier_feed,
        &OperatorPrincipal {
            certificate: None,
            tenants: None,
            subject: Some("test:recovery-admin".into()),
            role: Some(RECOVERY_ADMIN_ROLE.into()),
            mtls: false,
        },
    )
    .await
}

/// Handles one bounded request over either raw loopback TCP or an authenticated TLS stream.
#[allow(clippy::too_many_arguments)]
async fn handle_http_connection_with_principal<S>(
    stream: &mut S,
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &Arc<Mutex<()>>,
    clock: &(impl Clock + Sync),
    deployment_feasibility: Option<&DeploymentFeasibility>,
    verifier_feed: Option<&TaskVerifierFeed>,
    principal: &OperatorPrincipal,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    super::network::isolate_synchronous_polls(apply_http_request(
        stream,
        controller,
        event_log,
        event_write_gate,
        clock,
        deployment_feasibility,
        verifier_feed,
        principal,
    ))
    .await
}

/// Reduces the original request with unchanged authority checks and asynchronous socket I/O.
#[allow(clippy::too_many_arguments)]
async fn apply_http_request<S>(
    stream: &mut S,
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &Arc<Mutex<()>>,
    clock: &(impl Clock + Sync),
    deployment_feasibility: Option<&DeploymentFeasibility>,
    verifier_feed: Option<&TaskVerifierFeed>,
    principal: &OperatorPrincipal,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    // Observation cannot add a blocking lock or a poisoned-lock failure to request handling.
    let diagnostics = controller
        .try_lock()
        .map(|live| live.bridge.diagnostics().clone())
        .unwrap_or_else(|_| DiagnosticHandle::default());
    let _request = StageObservation::start(
        &diagnostics,
        DiagnosticKind::Http,
        DiagnosticStage::Request,
        None,
    );
    let request = match tokio::time::timeout(
        CONTROL_HTTP_REQUEST_TIMEOUT,
        read_control_http_request(stream),
    )
    .await
    {
        Ok(Ok(request)) => request,
        Ok(Err(error)) => {
            return write_http_response(
                stream,
                "400 Bad Request",
                serde_json::json!({"error": error}),
            )
            .await;
        }
        Err(_) => {
            return write_http_response(
                stream,
                "408 Request Timeout",
                serde_json::json!({"error": "control HTTP request timed out"}),
            )
            .await;
        }
    };
    let request_body = match std::str::from_utf8(&request.body) {
        Ok(body) => body,
        Err(_) => {
            return write_http_response(
                stream,
                "400 Bad Request",
                serde_json::json!({"error": "control HTTP body is not UTF-8"}),
            )
            .await;
        }
    };
    let method = request.method.as_str();
    let target = request.target.as_str();
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    let current_principal = match refresh_principal(
        principal,
        controller,
        event_log,
        event_write_gate,
        clock.now(),
    ) {
        Ok(current) => current,
        Err(error) => {
            return write_http_response(
                stream,
                "401 Unauthorized",
                serde_json::json!({"error":error}),
            )
            .await;
        }
    };
    let principal = &current_principal;
    if let Err((status, error)) = authorize_operator_request(&request, principal, method, path) {
        return write_http_response(stream, status, serde_json::json!({"error":error})).await;
    }
    let tenant = match authorized_tenant(&request, principal) {
        Ok(tenant) => tenant,
        Err(error) => {
            return write_http_response(
                stream,
                "403 Forbidden",
                serde_json::json!({"error":error}),
            )
            .await;
        }
    };
    if !matches!(path, "/healthz" | "/readyz") && event_log.require_read_authority().is_err() {
        return write_http_response(
            stream,
            "503 Service Unavailable",
            serde_json::json!({"error":"current Controller epoch or quorum is unavailable"}),
        )
        .await;
    }
    let tenant_check = {
        let live = controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?;
        authorize_tenant_path(&live, tenant.as_deref(), path)
    };
    if let Err(error) = tenant_check {
        return write_http_response(stream, "403 Forbidden", serde_json::json!({"error":error}))
            .await;
    }
    let missing_scoped_registry = tenant.is_some()
        && method == "POST"
        && matches!(
            path,
            "/v1/missions" | "/v1/missions/assess-initial-support" | "/v1/missions/validate-plan"
        )
        && controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?
            .bridge
            .control()
            .node_identity_registry()
            .is_none();
    if missing_scoped_registry {
        return write_http_response(stream,"503 Service Unavailable",serde_json::json!({"error":"tenant-scoped admission requires the lifecycle Node identity registry"})).await;
    }
    let authorization_time = if principal.certificate.is_some() {
        clock.now()
    } else {
        domain::TimestampMs::new(0)
    };
    let authorized_write_gate = Arc::new(AuthorizedRequestGate::new(
        event_write_gate.clone(),
        controller.clone(),
        principal.clone(),
        &request,
        tenant.clone(),
        authorization_time,
    ));
    let event_write_gate = &authorized_write_gate;
    if method == "GET" && path == "/v1/authority-status" {
        let body = serde_json::to_value(event_log.write_status()?)?;
        return write_http_response(stream, "200 OK", body).await;
    }
    if method == "POST" && path == "/v1/authority/recover" {
        let command = match serde_json::from_slice::<state::WriteRecoveryCommand>(&request.body) {
            Ok(command) if request.body.len() <= 4096 => command,
            _ => {
                return write_http_response(
                    stream,
                    "400 Bad Request",
                    serde_json::json!({"error":"invalid bounded write-recovery command"}),
                )
                .await;
            }
        };
        let subject = principal
            .subject
            .clone()
            .unwrap_or_else(|| "trusted-loopback:recovery-admin".into());
        let shared = controller.clone();
        let gate = event_write_gate.clone();
        let log = event_log.clone();
        let result = tokio::task::spawn_blocking(move || {
            let _guard = gate.lock().map_err(|error| ("403 Forbidden", error))?;
            {
                let live = shared.lock().map_err(|_| {
                    (
                        "503 Service Unavailable",
                        "controller lock is poisoned".into(),
                    )
                })?;
                if let Some(catalog) = &live.memory_preparation.catalog {
                    catalog
                        .require_recovery_projection()
                        .map_err(|error| ("503 Service Unavailable", error))?;
                }
            }
            log.recover_writes(&command, &subject).map_err(|error| {
                let status = match &error {
                    state::SqliteEventLogError::Codec(_)
                    | state::SqliteEventLogError::WriteDisabled(_) => "409 Conflict",
                    _ => "503 Service Unavailable",
                };
                (status, error.to_string())
            })
        })
        .await
        .map_err(|error| format!("write-recovery worker failed: {error}"))?;
        return match result {
            Ok(receipt) => {
                write_http_response(stream, "200 OK", serde_json::to_value(receipt)?).await
            }
            Err((status, error)) => {
                write_http_response(stream, status, serde_json::json!({"error":error})).await
            }
        };
    }
    if method != "GET"
        && !matches!(
            path,
            "/v1/missions/assess-initial-support" | "/v1/missions/validate-plan"
        )
        && let Err(error) = event_log.require_writes_enabled()
    {
        return write_http_response(
            stream,
            "503 Service Unavailable",
            serde_json::json!({"error":error.to_string(),"authority":event_log.write_status()?}),
        )
        .await;
    }
    if method == "GET" && path == "/metrics" {
        let metrics = controller
            .lock()
            .map_err(|_| "controller lock is poisoned".to_string())
            .map(|live| live.clone())
            .and_then(|snapshot| controller_metrics(&snapshot, event_log, clock.now()));
        return match metrics {
            Ok(metrics) => write_text_response(stream, "200 OK", &metrics).await,
            Err(error) => {
                write_http_response(
                    stream,
                    "503 Service Unavailable",
                    serde_json::json!({"error": error}),
                )
                .await
            }
        };
    }
    if method == "POST"
        && (path == "/v1/memory/handoffs"
            || path.starts_with("/v1/memory/handoffs/") && path.ends_with("/commit"))
    {
        let commit_id = path
            .strip_prefix("/v1/memory/handoffs/")
            .and_then(|id| id.strip_suffix("/commit"))
            .map(str::to_owned);
        let shared = controller.clone();
        let log = event_log.clone();
        let gate = event_write_gate.clone();
        let body = request.body.clone();
        let subject = principal
            .subject
            .as_deref()
            .unwrap_or("trusted-loopback:recovery-admin")
            .to_string();
        let tenant = tenant.clone();
        let worker_clock = application::memory_use_query::AdmissionClock::new(clock.now());
        let result = tokio::task::spawn_blocking(move || {
            application::memory_handoff::submit(
                &shared,
                &log,
                &gate,
                &worker_clock,
                &body,
                &subject,
                tenant.as_deref(),
                commit_id.as_deref(),
            )
        })
        .await
        .map_err(|error| format!("handoff worker failed: {error}"))?;
        return match result {
            Ok(body) => write_http_response(stream, "202 Accepted", body).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error":error}))
                    .await
            }
        };
    }
    if method == "GET" && path == "/v1/memory/handoffs" {
        let parameters = parse_query(query);
        let limit = parameters
            .get("limit")
            .map(|value| value.parse::<usize>())
            .transpose();
        let Ok(limit) = limit else {
            return write_http_response(
                stream,
                "400 Bad Request",
                serde_json::json!({"error":"limit must be an integer"}),
            )
            .await;
        };
        let body = {
            let live = controller
                .lock()
                .map_err(|_| "handoff authority unavailable")?;
            application::memory_handoff::view(
                &live,
                tenant.as_deref(),
                parameters.get("after").copied(),
                limit.unwrap_or(32),
            )
        };
        return write_http_response(stream, "200 OK", body).await;
    }
    if method == "POST" && path == "/v1/memory/use-queries" {
        let shared = controller.clone();
        let log = event_log.clone();
        let gate = event_write_gate.clone();
        let body = request.body.clone();
        let subject = principal
            .subject
            .as_deref()
            .unwrap_or("trusted-loopback:recovery-admin")
            .to_string();
        let tenant = tenant.clone();
        let worker_clock = application::memory_use_query::AdmissionClock::new(clock.now());
        let result = tokio::task::spawn_blocking(move || {
            application::memory_use_query::submit(
                &shared,
                &log,
                &gate,
                &worker_clock,
                &body,
                &subject,
                tenant.as_deref(),
            )
        })
        .await
        .map_err(|error| format!("readonly use query worker failed: {error}"))?;
        return match result {
            Ok(body) => write_http_response(stream, "202 Accepted", body).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error":error}))
                    .await
            }
        };
    }
    if method == "GET" && path == "/v1/memory/use-queries" {
        let parameters = parse_query(query);
        let after = parameters.get("after").copied();
        let limit = parameters
            .get("limit")
            .map(|v| v.parse::<usize>())
            .transpose();
        let Ok(limit) = limit else {
            return write_http_response(
                stream,
                "400 Bad Request",
                serde_json::json!({"error":"limit must be an integer"}),
            )
            .await;
        };
        let body = {
            let live = controller
                .lock()
                .map_err(|_| "Controller authority unavailable")?;
            application::memory_use_query::view(
                &live,
                tenant.as_deref(),
                after,
                limit.unwrap_or(32),
            )
        };
        return write_http_response(stream, "200 OK", body).await;
    }
    if method == "POST" && path == "/v1/memory/import-queries" {
        let result = application::memory_query::submit(
            controller,
            event_log,
            event_write_gate,
            clock,
            &request.body,
            principal
                .subject
                .as_deref()
                .unwrap_or("trusted-loopback:recovery-admin"),
        );
        return match result {
            Ok(body) => write_http_response(stream, "202 Accepted", body).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error": error}))
                    .await
            }
        };
    }
    if method == "POST" && path == "/v1/memory/imports" {
        let result = application::memory_import::submit(
            controller,
            event_log,
            event_write_gate,
            clock,
            &request.body,
            principal
                .subject
                .as_deref()
                .unwrap_or("trusted-loopback:recovery-admin"),
        );
        return match result {
            Ok(body) => write_http_response(stream, "202 Accepted", body).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error":error}))
                    .await
            }
        };
    }
    if method == "POST" && path == "/v1/node-identities/commands" {
        let subject = principal
            .subject
            .as_deref()
            .unwrap_or("trusted-loopback:recovery-admin");
        let result = application::node_identity::submit(
            controller,
            event_log,
            event_write_gate,
            clock,
            &request.body,
            subject,
        );
        return match result {
            Ok(body) => write_http_response(stream, "200 OK", body).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error": error}))
                    .await
            }
        };
    }
    if method == "POST" && path == "/v1/artifact-authorizations" {
        return match application::state_policy::submit_artifact(
            controller,
            event_log,
            event_write_gate,
            &request.body,
            principal
                .subject
                .as_deref()
                .unwrap_or("trusted-loopback:recovery-admin"),
            clock.now(),
        ) {
            Ok(result) => write_http_response(stream, "200 OK", result).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error":error}))
                    .await
            }
        };
    }
    if method == "POST" && path == "/v1/memory-authorizations" {
        return match application::state_policy::submit_memory(
            controller,
            event_log,
            event_write_gate,
            &request.body,
            principal
                .subject
                .as_deref()
                .unwrap_or("trusted-loopback:recovery-admin"),
            clock.now(),
        ) {
            Ok(result) => write_http_response(stream, "200 OK", result).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error":error}))
                    .await
            }
        };
    }
    if method == "POST" && path == "/v1/state-policies" {
        return match application::state_policy::submit(
            controller,
            event_log,
            event_write_gate,
            request_body.as_bytes(),
            principal
                .subject
                .as_deref()
                .unwrap_or("trusted-loopback:recovery-admin"),
            clock.now(),
        ) {
            Ok(result) => write_http_response(stream, "200 OK", result).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error":error}))
                    .await
            }
        };
    }
    if method == "POST" && path == "/v1/belief-policies" {
        return match application::state_policy::submit_beliefs(
            controller,
            event_log,
            event_write_gate,
            &request.body,
            principal
                .subject
                .as_deref()
                .unwrap_or("trusted-loopback:recovery-admin"),
            clock.now(),
        ) {
            Ok(result) => write_http_response(stream, "200 OK", result).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error":error}))
                    .await
            }
        };
    }
    if method == "POST" && path == "/v1/verifier-sources" {
        return match application::state_policy::submit_verifiers(
            controller,
            event_log,
            event_write_gate,
            &request.body,
            principal
                .subject
                .as_deref()
                .unwrap_or("trusted-loopback:recovery-admin"),
            clock.now(),
        ) {
            Ok(result) => write_http_response(stream, "200 OK", result).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error":error}))
                    .await
            }
        };
    }
    if method == "POST" && path == "/v1/verifier-evidence" {
        return match application::semantic_verifier::submit(
            controller,
            event_log,
            event_write_gate,
            clock,
            &request.body,
            principal
                .subject
                .as_deref()
                .unwrap_or("trusted-loopback:operator"),
            tenant.as_deref(),
        ) {
            Ok(result) => write_http_response(stream, "200 OK", result).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error":error}))
                    .await
            }
        };
    }
    if method == "POST" && path == "/v1/tenant-migrations" {
        let result = application::tenant::migrate(
            controller,
            event_log,
            event_write_gate,
            clock,
            request_body.as_bytes(),
            principal
                .subject
                .as_deref()
                .unwrap_or("loopback:recovery-admin"),
        );
        return match result {
            Ok(receipt) => write_http_response(stream, "200 OK", receipt).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error":error}))
                    .await
            }
        };
    }
    if method == "POST"
        && matches!(
            path,
            "/v1/attestation/challenges" | "/v1/attestation/evidence"
        )
    {
        let result = application::attestation::submit(
            controller,
            event_log,
            event_write_gate,
            clock,
            &request.body,
            principal
                .subject
                .as_deref()
                .unwrap_or("trusted-loopback:recovery-admin"),
            path.ends_with("/challenges"),
        );
        return match result {
            Ok(body) => write_http_response(stream, "200 OK", body).await,
            Err(error) => {
                write_http_response(stream, "409 Conflict", serde_json::json!({"error": error}))
                    .await
            }
        };
    }
    if method == "GET" && path.starts_with("/v1/memory/imports/") {
        let id = path.trim_start_matches("/v1/memory/imports/");
        let response = controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?
            .memory_imports
            .get(id)
            .map(|intent| serde_json::to_value(intent).expect("typed durable Memory intent"));
        return match response {
            Some(body) => write_http_response(stream, "200 OK", body).await,
            None => {
                write_http_response(
                    stream,
                    "404 Not Found",
                    serde_json::json!({"error":"unknown Memory import"}),
                )
                .await
            }
        };
    }
    let (status, body) = match (method, path) {
        ("GET", "/healthz") => ("200 OK", serde_json::json!({"status": "ok"})),
        ("GET", "/readyz") => match event_log.readiness() {
            Ok(()) => {
                let live = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let unknown = live.bridge.current_unknown_attempts().len();
                (
                    "200 OK",
                    serde_json::json!({"ready":true,"degraded":unknown>0,"unknown_attempts":unknown}),
                )
            }
            Err(_) => (
                "503 Service Unavailable",
                serde_json::json!({"ready":false,"reason":"authority storage or quorum unavailable"}),
            ),
        },
        ("GET", "/v1/memory/use-leases") => {
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                serde_json::json!({
                    "schema_version":"roboguide.memory-use-view/v0.2", "records":live.memory_use.records,
                    "provider_progress":application::memory_use_query::progress(&live,clock.now()),
                    "receipt_is_physical_stop_proof":false,
                }),
            )
        }
        ("GET", "/v1/memory/preparations") => {
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                serde_json::json!({
                    "schema_version":"roboguide.memory-preparation-view/v0.1",
                    "records":live.memory_preparation.records,
                    "receipt_is_physical_stop_proof":false,
                }),
            )
        }
        ("GET", "/v1/recovery") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            ("200 OK", recovery_view(&controller, clock.now()))
        }
        ("GET", "/v1/node-identities") => {
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            let mut registry = live.bridge.control().node_identity_registry().cloned();
            if let (Some(registry), Some(tenant)) = (&mut registry, tenant.as_deref()) {
                // The retired-leaf ledger is global administrative evidence, not tenant discovery.
                registry.retired_digests.clear();
                registry
                    .records
                    .retain(|_, identity| identity.tenant_id == tenant);
                registry
                    .commands
                    .retain(|_, command| registry.records.contains_key(&command.node_id));
                registry
                    .attestation_challenges
                    .retain(|_, challenge| registry.records.contains_key(&challenge.node_id));
            }
            (
                "200 OK",
                serde_json::json!({
                    "schema_version": "roboguide.node-identity-view/v0.1",
                    "registry": registry,
                    "physical_identity": "HardwareEvidencePending",
                }),
            )
        }
        ("GET", "/v1/artifact-authorizations") => {
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                serde_json::json!({"schema_version":"roboguide.artifact-authorization-view/v0.1", "registry":live.bridge.control().artifact_authorization_registry()}),
            )
        }
        ("GET", "/v1/memory-authorizations") => {
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                serde_json::json!({"schema_version":"roboguide.memory-authorization-view/v0.1",
                "registry":live.bridge.control().memory_authorization_registry()}),
            )
        }
        ("GET", "/v1/belief-policies") => {
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                serde_json::json!({"schema_version":"roboguide.belief-policy-view/v0.1", "registry":live.bridge.control().belief_policy_registry()}),
            )
        }
        ("GET", "/v1/verifier-sources") => {
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                serde_json::json!({"schema_version":"roboguide.semantic-verifier-view/v0.1",
                "registry":live.bridge.control().semantic_verifier_registry()}),
            )
        }
        ("GET", "/v1/verifier-evidence") => {
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            let receipts = live
                .bridge
                .control()
                .semantic_verifier_receipts()
                .into_iter()
                .filter(|receipt| {
                    tenant
                        .as_deref()
                        .is_none_or(|tenant| tenant == receipt.evidence.tenant_id)
                })
                .collect::<Vec<_>>();
            (
                "200 OK",
                serde_json::json!({"schema_version":"roboguide.semantic-verifier-view/v0.1","receipts":receipts}),
            )
        }
        ("GET", "/v1/state-policies") => {
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                serde_json::json!({"schema_version":"roboguide.decision-policy-view/v0.1","registry":live.bridge.control().decision_policy_registry()}),
            )
        }
        ("GET", path) if path.starts_with("/v1/executions/") && path.ends_with("/safety-stop") => {
            let execution_id = path
                .trim_start_matches("/v1/executions/")
                .trim_end_matches("/safety-stop")
                .trim_end_matches('/');
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            match live.safety_stops.get(execution_id) {
                Some(purpose) => (
                    "200 OK",
                    serde_json::json!({
                        "schema_version": "roboguide.safety-stop-view/v0.1", "purpose": purpose,
                        "command_receipt_is_stop_proof": false,
                        "physical_device_validation": "HardwareEvidencePending",
                    }),
                ),
                None => (
                    "404 Not Found",
                    serde_json::json!({"error": "stop purpose unavailable"}),
                ),
            }
        }
        ("GET", "/v1/inventory") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            let mut view = inventory_json(controller.bridge.state(), clock.now());
            if let Some(tenant) = tenant.as_deref()
                && let Some(nodes) = view
                    .get_mut("nodes")
                    .and_then(serde_json::Value::as_array_mut)
            {
                nodes.retain(|node| {
                    node.get("node_id")
                        .and_then(serde_json::Value::as_str)
                        .is_some_and(|id| {
                            controller
                                .bridge
                                .control()
                                .node_identity_registry()
                                .is_some_and(|registry| {
                                    registry
                                        .records
                                        .get(id)
                                        .is_some_and(|identity| identity.tenant_id == tenant)
                                })
                        })
                });
            }
            ("200 OK", view)
        }
        ("GET", "/v1/state/providers") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                state_providers_json(&controller, tenant.as_deref()),
            )
        }
        ("GET", "/v1/memory/providers") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            ("200 OK", memory_providers_json(&controller))
        }
        ("GET", "/v1/state/records") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                state_records_json_with_tenant(
                    &controller,
                    clock.now(),
                    &parse_query(query),
                    tenant.as_deref(),
                ),
            )
        }
        ("GET", "/v1/events") => {
            let query = parse_query(query);
            let after_sequence = query
                .get("after")
                .and_then(|value| value.parse::<u64>().ok());
            let limit = query
                .get("limit")
                .and_then(|value| value.parse::<usize>().ok())
                .unwrap_or(100);
            let log = event_log.clone();
            let gate = event_write_gate.clone();
            // The shared connection must never expose an application's uncommitted batch.
            // Wait off the async executor, then hold the existing write gate only for the read.
            let events = tokio::task::spawn_blocking(move || {
                let _guard = gate.lock()?;
                log.events_page(after_sequence, limit)
                    .map_err(|error| error.to_string())
            })
            .await
            .unwrap_or_else(|_| Err("event-log reader failed".to_string()));
            match events {
                Ok(events) => {
                    let records = events
                        .iter()
                        .map(|event| {
                            let payload = serde_json::from_str(&event.payload_json)
                                .unwrap_or_else(|_| serde_json::json!(event.payload_json));
                            serde_json::json!({
                                "sequence": event.sequence,
                                "event_id": event.event_id,
                                "timestamp_ms": event.timestamp_ms,
                                "correlation_id": event.correlation_id,
                                "causation_id": event.causation_id,
                                "payload_schema": event.payload_schema,
                                "payload": payload,
                            })
                        })
                        .collect::<Vec<_>>();
                    ("200 OK", serde_json::json!({"events": records}))
                }
                Err(error) => (
                    "503 Service Unavailable",
                    serde_json::json!({"error": error}),
                ),
            }
        }
        ("GET", "/v1/maintenance/archive") => {
            let log = event_log.clone();
            let gate = event_write_gate.clone();
            let query = parse_query(query);
            let after = query
                .get("after")
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(0);
            let limit = query
                .get("limit")
                .and_then(|value| value.parse::<usize>().ok())
                .unwrap_or(128);
            let status = tokio::task::spawn_blocking(move || {
                let _guard = gate.lock().map_err(|_|"event-log write gate is poisoned".to_string())?;
                Ok::<_, String>(serde_json::json!({
                    "schema_version": "roboguide.event-archive-status/v0.1",
                    "latest_sequence": log.latest_sequence().map_err(|error| error.to_string())?,
                    "history_floor": log.history_floor().map_err(|error| error.to_string())?,
                    "retained_events": log.len().map_err(|error| error.to_string())?,
                    "manifest": log.archive_manifest().map_err(|error| error.to_string())?,
                    "manifests": log.archive_manifests(after,limit).map_err(|error|error.to_string())?,
                    "manifest_page_limit": limit.clamp(1,128),
                    "max_segment_events": state::MAX_ARCHIVE_EVENTS,
                    "max_segment_bytes": state::MAX_ARCHIVE_BYTES,
                    "catalog_and_localization_history_gc": "unsupported_until_recovery_contract_exists",
                    "automatic_gc": false,
                    "replicated_writer_requires_coordinated_maintenance": log.replication_profile().is_some(),
                }))
            })
            .await
            .map_err(|error| format!("archive status worker failed: {error}"))?;
            match status {
                Ok(status) => ("200 OK", status),
                Err(error) => (
                    "503 Service Unavailable",
                    serde_json::json!({"error": error}),
                ),
            }
        }
        ("POST", "/v1/maintenance/archive") => {
            if operator_role(&request, principal).as_deref() != Some(RECOVERY_ADMIN_ROLE) {
                return write_http_response(
                    stream,
                    "403 Forbidden",
                    serde_json::json!({"error": "event archive/GC requires the recovery-admin role"}),
                )
                .await;
            }
            let body: serde_json::Value = match serde_json::from_str(request_body) {
                Ok(body) => body,
                Err(_) => {
                    return write_http_response(
                        stream,
                        "400 Bad Request",
                        serde_json::json!({"error": "invalid archive JSON"}),
                    )
                    .await;
                }
            };
            let cutoff = body
                .get("end_sequence")
                .and_then(serde_json::Value::as_u64)
                .filter(|cutoff| *cutoff > 0);
            let Some(cutoff) = cutoff else {
                return write_http_response(
                    stream,
                    "400 Bad Request",
                    serde_json::json!({"error": "archive requires one positive end_sequence"}),
                )
                .await;
            };
            let archive_root = std::env::var_os("ROBOGUIDE_EVENT_ARCHIVE_DIRECTORY")
                .map(std::path::PathBuf::from)
                .filter(|path| path.is_absolute());
            let Some(archive_root) = archive_root else {
                return write_http_response(
                    stream,
                    "503 Service Unavailable",
                    serde_json::json!({"error": "ROBOGUIDE_EVENT_ARCHIVE_DIRECTORY must be an absolute deployment-owned path"}),
                )
                .await;
            };
            let log = event_log.clone();
            let gate = event_write_gate.clone();
            let shared_controller = controller.clone();
            let result = tokio::task::spawn_blocking(move || {
                let _write_guard = gate
                    .lock()
                    .map_err(|_| state::SqliteEventLogError::LockPoisoned)?;
                let live = shared_controller.lock().map_err(|_|state::SqliteEventLogError::LockPoisoned)?;
                if live.orchestrator.mission_ids().iter().any(|mission| live.orchestrator.execution(mission).is_some_and(|execution| matches!(execution.lifecycle(),orchestration::MissionExecutionLifecycle::Accepted | orchestration::MissionExecutionLifecycle::Running | orchestration::MissionExecutionLifecycle::Cancelling))) || !live.bridge.current_unknown_attempts().is_empty() {
                    return Err(state::SqliteEventLogError::ArchivePolicy("archive maintenance requires no nonterminal Missions or unknown physical attempts".into()));
                }
                drop(live);
                log.archive_and_gc(cutoff, &state::FileArchiveSink::new(archive_root))
            })
            .await
            .map_err(|error| format!("archive worker failed: {error}"))?;
            match result {
                Ok(manifest) => (
                    "200 OK",
                    serde_json::json!({"status": "archived_and_compacted", "manifest": manifest}),
                ),
                Err(state::SqliteEventLogError::ArchivePolicy(error)) => (
                    "409 Conflict",
                    serde_json::json!({"error": error, "rows_deleted": false}),
                ),
                Err(error) => (
                    "503 Service Unavailable",
                    serde_json::json!({"error":error.to_string(),"maintenance_outcome":"not_confirmed"}),
                ),
            }
        }
        ("POST", "/v1/missions/assess-initial-support") => {
            let plan = match decode_mission_plan(request_body) {
                Ok(plan) => plan,
                Err(error) => {
                    return write_http_response(
                        stream,
                        "400 Bad Request",
                        serde_json::json!({"error": error.to_string()}),
                    )
                    .await;
                }
            };
            let mut scoped_control = {
                let live = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                live.bridge.control().clone()
            };
            if let Err(error) = scoped_control.register_mission_tenant(
                plan.goal().mission_id().clone(),
                tenant.as_deref().unwrap_or("local"),
            ) {
                return write_http_response(
                    stream,
                    "403 Forbidden",
                    serde_json::json!({"error":error.to_string()}),
                )
                .await;
            }
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            let assessment = match deployment_feasibility {
                Some(source) => source.assess_initial_plan(
                    &plan,
                    &scoped_control,
                    live.bridge.state(),
                    clock.now(),
                    live.orchestrator.mission_ids().is_empty(),
                    request_body.as_bytes(),
                ),
                None => unavailable_initial_assessment(
                    &plan,
                    request_body.as_bytes(),
                    clock.now(),
                    "source_unconfigured",
                ),
            };
            ("200 OK", assessment)
        }
        ("GET", path) if path.starts_with("/v1/missions/") && path.ends_with("/tenant") => {
            let mission = domain::MissionId::new(
                path.trim_start_matches("/v1/missions/")
                    .trim_end_matches("/tenant"),
            )?;
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            match application::tenant::source_digest(&live, &mission) {
                Ok(digest) => (
                    "200 OK",
                    serde_json::json!({"mission_id":mission,"tenant_id":live.bridge.control().mission_tenant(&mission),"source_plan_digest":digest}),
                ),
                Err(error) => ("404 Not Found", serde_json::json!({"error":error})),
            }
        }
        ("POST", "/v1/missions/validate-plan") => {
            let plan = match decode_mission_plan(request_body) {
                Ok(plan) => plan,
                Err(error) => {
                    return write_http_response(
                        stream,
                        "400 Bad Request",
                        serde_json::json!({"error":error.to_string()}),
                    )
                    .await;
                }
            };
            let result = {
                let live = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let mut scoped = live.bridge.control().clone();
                scoped
                    .register_mission_tenant(
                        plan.goal().mission_id().clone(),
                        tenant.as_deref().unwrap_or("local"),
                    )
                    .map(|_| {
                        application::plan_validation::assess(
                            &live,
                            &plan,
                            tenant.as_deref().unwrap_or("local"),
                            deployment_feasibility,
                            verifier_feed,
                            &request.body,
                        )
                    })
            };
            match result {
                Ok(result) => ("200 OK", result),
                Err(error) => (
                    "403 Forbidden",
                    serde_json::json!({"error":error.to_string()}),
                ),
            }
        }
        ("POST", "/v1/missions") => {
            let plan = match decode_mission_plan(request_body) {
                Ok(plan) => plan,
                Err(error) => {
                    return write_http_response(
                        stream,
                        "400 Bad Request",
                        serde_json::json!({"error": error.to_string()}),
                    )
                    .await;
                }
            };
            let verifier_admission = {
                let live = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                application::semantic_verifier::validate_plan(
                    &live,
                    &plan,
                    tenant.as_deref().unwrap_or("local"),
                    verifier_feed,
                )
            };
            if let Err(error) = verifier_admission {
                return write_http_response(
                    stream,
                    "422 Unprocessable Entity",
                    serde_json::json!({"error": error}),
                )
                .await;
            }
            let mission_id = plan.goal().mission_id().clone();
            let group_id = domain::ExecutionGroupId::new(format!("group-{mission_id}"))?;
            let gate_wait = StageObservation::start(
                &diagnostics,
                DiagnosticKind::Http,
                DiagnosticStage::WriteGate,
                None,
            );
            let _write_guard = event_write_gate.lock()?;
            drop(gate_wait);
            let capacity = {
                let live = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                live.orchestrator.validate_admission_capacity(&plan)
            };
            if let Err(error) = capacity {
                let status = if matches!(error, OrchestrationError::AdmissionCapacityExceeded) {
                    "429 Too Many Requests"
                } else {
                    "409 Conflict"
                };
                drop(_write_guard);
                return write_http_response(
                    stream,
                    status,
                    serde_json::json!({"error": error.to_string()}),
                )
                .await;
            }
            event_log.begin_batch()?;
            let now = clock.now();
            let mut pending_controller = None;
            let result: Result<String, String> = {
                let controller = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let clone_sample = StageObservation::start(
                    controller.bridge.diagnostics(),
                    DiagnosticKind::Http,
                    DiagnosticStage::Clone,
                    None,
                );
                let mut candidate = controller.clone();
                drop(clone_sample);
                drop(controller);
                let mut events = event_log.clone();
                let operation = {
                    let ControllerState {
                        bridge,
                        orchestrator,
                        ..
                    } = &mut candidate;
                    bridge
                        .control_mut()
                        .register_mission_tenant(
                            mission_id.clone(),
                            tenant.as_deref().unwrap_or("local"),
                        )
                        .map_err(|error| error.to_string())?;
                    let generic = {
                        let control = bridge.control();
                        plan.task_graph().tasks().iter().all(|task| {
                            match task.satisfaction_basis() {
                                domain::TaskSatisfactionBasis::ExecutionReport => true,
                                domain::TaskSatisfactionBasis::VerifierEvidence(spec) => control
                                    .registered_verifier_source(
                                        spec,
                                        tenant.as_deref().unwrap_or("local"),
                                    )
                                    .is_ok(),
                            }
                        })
                    };
                    if generic {
                        bridge
                            .control_mut()
                            .freeze_mission_verifier_sources(
                                &plan,
                                tenant.as_deref().unwrap_or("local"),
                            )
                            .map_err(|error| error.to_string())?;
                    } else {
                        verifier_feed
                            .ok_or("verifier source changed during admission")?
                            .validate_plan(&plan)?;
                    }
                    let first_mission = orchestrator.mission_ids().is_empty();
                    let eligibility = deployment_feasibility
                        .map(|snapshot| snapshot.restrictions_for_plan(&plan, &group_id))
                        .transpose();
                    eligibility
                        .and_then(|restrictions| {
                            if let Some(restrictions) = restrictions {
                                let snapshot =
                                    deployment_feasibility.expect("restriction has source");
                                for (actor_id, nodes) in restrictions {
                                    bridge
                                        .control_mut()
                                        .set_actor_candidate_restriction(
                                            mission_id.clone(),
                                            actor_id,
                                            nodes,
                                            snapshot.digest().to_string(),
                                        )
                                        .map_err(|error| error.to_string())?;
                                }
                            }
                            Ok(())
                        })
                        .and_then(|_| validate_actor_placement_coverage(bridge.control(), &plan))
                        .and_then(|_| {
                            let submit_correlation =
                                domain::CorrelationId::new(format!("submit-{mission_id}"))
                                    .map_err(|error| error.to_string())?;
                            orchestrator
                                .submit(
                                    plan.clone(),
                                    group_id.clone(),
                                    bridge.control_mut(),
                                    now,
                                    &submit_correlation,
                                    &mut events,
                                )
                                .map_err(|error| error.to_string())?;
                            if first_mission && let Some(snapshot) = deployment_feasibility
                                && let Some(preferences) = snapshot.initial_preferences_for_plan(
                                    &plan, bridge.control(), bridge.state(), now, &submit_correlation, &mut events,
                                )?
                            {
                                let source = preferences.source_digest().to_string();
                                bridge.control_mut().set_initial_candidate_preferences(&plan, preferences)
                                    .map_err(|error| error.to_string())?;
                                eprintln!("initial candidate preferences admitted for {mission_id}: {source}; commitment remains pending");
                            }
                            bridge
                                .register_execution_relations(
                                    &plan,
                                    &group_id,
                                    now,
                                    &submit_correlation,
                                )
                                .map_err(|error| error.to_string())
                        })
                };
                operation
                    .and_then(|_| {
                        let dispatch_correlation =
                            domain::CorrelationId::new(format!("dispatch-{mission_id}"))
                                .map_err(|error| error.to_string())?;
                        drive_ready_tasks(&mut candidate, now, &dispatch_correlation, &mut events)
                            .map_err(|error| error.to_string())
                    })
                    .and_then(|_| {
                        application::semantic_verifier::monitor_active(&mut candidate, now)
                    })
                    .and_then(|_| {
                        candidate
                            .mission_admissions
                            .entry(mission_id.as_str().to_string())
                            .or_insert_with(|| {
                                controller_http::MissionAdmission::new(
                                    mission_id.clone(),
                                    group_id.clone(),
                                    request_body.as_bytes(),
                                    now,
                                )
                            });
                        crate::diagnostics::http_checkpoint(&candidate)
                            .map_err(|error| error.to_string())
                    })
                    .inspect(|_| pending_controller = Some(candidate))
            };
            match result {
                Ok(checkpoint_json) => {
                    if let Err(error) =
                        crate::diagnostics::http_save(event_log, &checkpoint_json, &diagnostics)
                    {
                        let rollback = event_log.rollback_batch();
                        drop(_write_guard);
                        return write_http_response(
                            stream,
                            "503 Service Unavailable",
                            serde_json::json!({"error": format!("checkpoint persistence failed: {error}; rollback: {}", rollback.as_ref().err().map(ToString::to_string).unwrap_or_else(|| "ok".to_string()))}),
                        ).await;
                    }
                    if let Err(error) = crate::diagnostics::http_commit(event_log, &diagnostics) {
                        let rollback = event_log.rollback_batch();
                        drop(_write_guard);
                        return write_http_response(
                            stream,
                            "503 Service Unavailable",
                            serde_json::json!({"error": format!("event batch commit failed: {error}; rollback: {}", rollback.as_ref().err().map(ToString::to_string).unwrap_or_else(|| "ok".to_string()))}),
                        ).await;
                    }
                    if let Some(candidate) = pending_controller {
                        let mut live = controller
                            .lock()
                            .map_err(|_| "controller lock is poisoned")?;
                        *live = candidate;
                        if let Err(error) = flush_unisolated_commands(&mut live, clock) {
                            eprintln!("durable command outbox delivery deferred: {error}");
                        }
                    }
                    (
                        "202 Accepted",
                        serde_json::json!({
                            "mission_id": mission_id.as_str(),
                            "group_id": group_id.as_str(),
                            "status": "Running"
                        }),
                    )
                }
                Err(error) => {
                    event_log.rollback_batch()?;
                    drop(_write_guard);
                    ("409 Conflict", serde_json::json!({"error": error}))
                }
            }
        }
        ("GET", path) if path.starts_with("/v1/missions/") && path.ends_with("/admission") => {
            let mission_text = path
                .trim_start_matches("/v1/missions/")
                .trim_end_matches("/admission");
            let mission_id = domain::MissionId::new(mission_text)?;
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            match controller.mission_admissions.get(mission_id.as_str()) {
                Some(admission) => {
                    admission.validate(&controller, mission_id.as_str())?;
                    ("200 OK", admission.to_json())
                }
                None => (
                    "404 Not Found",
                    serde_json::json!({"error": "admission receipt unavailable"}),
                ),
            }
        }
        ("GET", path) if path.starts_with("/v1/missions/") && path.ends_with("/recovery") => {
            let mission_text = path
                .trim_start_matches("/v1/missions/")
                .trim_end_matches("/recovery")
                .trim_end_matches('/');
            let mission_id = domain::MissionId::new(mission_text)?;
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            match live.orchestrator.execution(&mission_id) {
                Some(execution) => (
                    "200 OK",
                    serde_json::json!({
                        "schema_version": "roboguide.mission-recovery-view/v0.1",
                        "mission_id": mission_id.as_str(),
                        "group_id": execution.group_id().as_str(),
                        "status": format!("{:?}", execution.lifecycle()),
                        "isolation": execution.isolation(),
                        "failure_state": execution.failure_state(),
                        "crash_loop_quarantined": execution.is_crash_loop_quarantined(),
                        "review_due": execution.isolation().is_some_and(|hold| hold.is_review_due(clock.now())),
                        "new_execute_fenced": execution.isolation().is_some() || execution.is_crash_loop_quarantined(),
                        "ownership_retained": live.bridge.control().group(execution.group_id()).is_some_and(|group|group.lifecycle() != control::GroupLifecycle::Released),
                        "automatic_resource_release": false,
                        "automatic_node_replacement": false,
                    }),
                ),
                None => (
                    "404 Not Found",
                    serde_json::json!({"error": "unknown Mission"}),
                ),
            }
        }
        ("GET", path) if path.starts_with("/v1/missions/") => {
            let now = clock.now();
            let mission_text = path
                .trim_start_matches("/v1/missions/")
                .trim_end_matches('/');
            let mission_id = domain::MissionId::new(mission_text)?;
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            match controller.orchestrator.execution(&mission_id) {
                Some(execution) => {
                    let tasks = controller
                        .bridge
                        .control()
                        .group(execution.group_id())
                        .map(|group| {
                            group
                                .task_executions()
                                .map(|task| {
                                    serde_json::json!({
                                        "task_id": task.task_ref().task_id().as_str(),
                                        "context_id": task.context_id().as_str(),
                                        "status": format!("{:?}", task.lifecycle())
                                    })
                                })
                                .collect::<Vec<_>>()
                        })
                        .unwrap_or_default();
                    let relations = controller
                        .bridge
                        .relation_snapshots(execution.group_id())
                        .into_iter()
                        .map(|snapshot| {
                            let relation = snapshot.relation();
                            serde_json::json!({
                                "id": relation.relation_id().as_str(),
                                "kind": relation_kind_name(relation.kind()),
                                "source": {
                                    "task_id": relation.source_task_ref().task_id().as_str(),
                                    "role_id": relation.source_role_id().as_str(),
                                    "execution_id": snapshot.source_execution_id(),
                                },
                                "target": {
                                    "task_id": relation.target_task_ref().task_id().as_str(),
                                    "role_id": relation.target_role_id().as_str(),
                                    "execution_id": snapshot.target_execution_id(),
                                },
                                "state": format!("{:?}", snapshot.state()),
                                "reconciliation_required": snapshot.reconciliation_required(),
                            })
                        })
                        .collect::<Vec<_>>();
                    let peer_channels = controller
                        .bridge
                        .peer_channel_snapshots_at(execution.group_id(), now)
                        .into_iter()
                        .map(|channel| {
                            serde_json::json!({
                                "context_id": channel.context_id().as_str(),
                                "lifecycle": format!("{:?}", channel.lifecycle()),
                                "profile_id": channel.descriptor().profile_id,
                                "message_schema": channel.descriptor().message_schema,
                                "peers": channel.peers().iter().map(domain::ContextRoleId::as_str).collect::<Vec<_>>(),
                                "readiness": channel.readiness().iter().map(|evidence| serde_json::json!({
                                    "context_role_id": evidence.context_role_id.as_str(),
                                    "node_id": evidence.node_id.as_str(),
                                    "local_system_id": evidence.local_system_id.as_str(),
                                    "session_id": evidence.session_id,
                                    "channel_instance_id": evidence.channel_instance_id,
                                    "sequence": evidence.sequence,
                                    "received_at_ms": evidence.received_at.as_millis(),
                                    "expires_at_ms": evidence.expires_at.as_millis(),
                                    "ready": evidence.ready,
                                })).collect::<Vec<_>>(),
                            })
                        })
                        .collect::<Vec<_>>();
                    (
                        "200 OK",
                        serde_json::json!({
                            "mission_id": mission_id.as_str(),
                            "group_id": execution.group_id().as_str(),
                            "status": format!("{:?}", execution.lifecycle()),
                            "isolation": execution.isolation(),
                            "failure_state": execution.failure_state(),
                            "crash_loop_quarantined": execution.is_crash_loop_quarantined(),
                            "scheduling_policy": execution.dispatch_policy(),
                            "tasks": tasks,
                            "relations": relations,
                            "peer_channels": peer_channels
                        }),
                    )
                }
                None => (
                    "404 Not Found",
                    serde_json::json!({"error": "unknown Mission"}),
                ),
            }
        }
        ("POST", path)
            if path.starts_with("/v1/missions/") && path.ends_with("/scheduling-policy") =>
        {
            super::scheduling_policy::update_priority(
                controller,
                event_log,
                event_write_gate,
                clock,
                path,
                request_body,
                &diagnostics,
            )?
        }
        ("POST", path)
            if path.starts_with("/v1/missions/") && path.ends_with("/scheduling-preemption") =>
        {
            super::preemption::preempt(
                controller,
                event_log,
                event_write_gate,
                clock,
                path,
                request_body,
                &diagnostics,
            )?
        }
        ("POST", path) if path.starts_with("/v1/missions/") && path.ends_with("/resume") => {
            let body: serde_json::Value = match serde_json::from_str(request_body) {
                Ok(body) => body,
                Err(_) => {
                    return write_http_response(
                        stream,
                        "400 Bad Request",
                        serde_json::json!({"error": "invalid resume JSON"}),
                    )
                    .await;
                }
            };
            let generation = body
                .get("isolation_generation")
                .and_then(serde_json::Value::as_u64)
                .filter(|generation| *generation > 0);
            let Some(generation) = generation else {
                return write_http_response(stream, "400 Bad Request", serde_json::json!({"error": "resume requires one positive isolation_generation"})).await;
            };
            let acknowledge_crash_loop = body
                .get("acknowledge_crash_loop")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
            if body
                .get("acknowledge_crash_loop")
                .is_some_and(|value| !value.is_boolean())
            {
                return write_http_response(
                    stream,
                    "400 Bad Request",
                    serde_json::json!({"error":"acknowledge_crash_loop must be a boolean"}),
                )
                .await;
            }
            let reason = body
                .get("reason")
                .and_then(serde_json::Value::as_str)
                .filter(|reason| !reason.trim().is_empty() && reason.len() <= 512);
            if acknowledge_crash_loop && reason.is_none() {
                return write_http_response(stream,"400 Bad Request",serde_json::json!({"error":"crash-loop acknowledgement requires a bounded explicit reason"})).await;
            }
            let resume_role = operator_role(&request, principal);
            if acknowledge_crash_loop && resume_role.as_deref() != Some(RECOVERY_ADMIN_ROLE) {
                return write_http_response(
                    stream,
                    "403 Forbidden",
                    serde_json::json!({"error": "crash-loop acknowledgement requires the recovery-admin role"}),
                )
                .await;
            }
            let mission = domain::MissionId::new(
                path.trim_start_matches("/v1/missions/")
                    .trim_end_matches("/resume"),
            )?;
            let write_guard = event_write_gate.lock()?;
            if let Err(error) = event_log.begin_batch() {
                drop(write_guard);
                return write_http_response(
                    stream,
                    "503 Service Unavailable",
                    serde_json::json!({"error":format!("resume transaction unavailable: {error}")}),
                )
                .await;
            }
            let result: Result<(), (&str, String)> = (|| {
                let unavailable = |error: String| ("503 Service Unavailable", error);
                let live = controller
                    .lock()
                    .map_err(|_| unavailable("controller lock is poisoned".into()))?;
                let mut candidate = live.clone();
                drop(live);
                let now = clock.now();
                let correlation = domain::CorrelationId::new(format!("resume-{mission}"))
                    .map_err(|error| unavailable(error.to_string()))?;
                let resume = resume_mission_candidate_with_policy(
                    &mut candidate,
                    &mission,
                    generation,
                    now,
                    &correlation,
                    &mut event_log.clone(),
                    acknowledge_crash_loop,
                    resume_role.as_deref().unwrap_or("operator"),
                    reason.unwrap_or("generation-bound resume"),
                );
                if let Some(error) = event_log
                    .take_error()
                    .map_err(|error| unavailable(error.to_string()))?
                {
                    return Err(unavailable(format!("resume event sink failed: {error}")));
                }
                resume.map_err(|error| {
                    let status = if error.downcast_ref::<state::SqliteEventLogError>().is_some()
                        || error.downcast_ref::<serde_json::Error>().is_some()
                    {
                        "503 Service Unavailable"
                    } else {
                        "409 Conflict"
                    };
                    (status, error.to_string())
                })?;
                let checkpoint = crate::diagnostics::http_checkpoint(&candidate)
                    .map_err(|error| unavailable(error.to_string()))?;
                crate::diagnostics::http_save(event_log, &checkpoint, &diagnostics)
                    .map_err(|error| unavailable(error.to_string()))?;
                crate::diagnostics::http_commit(event_log, &diagnostics)
                    .map_err(|error| unavailable(error.to_string()))?;
                let mut live = controller
                    .lock()
                    .map_err(|_| unavailable("controller lock is poisoned".into()))?;
                *live = candidate;
                if let Err(error) = flush_unisolated_commands(&mut live, clock) {
                    eprintln!("durable resumed outbox deferred: {error}");
                }
                Ok(())
            })();
            let response = match result {
                Ok(()) => (
                    "200 OK",
                    serde_json::json!({"status": "resumed", "isolation_generation": generation, "crash_loop_acknowledged": acknowledge_crash_loop}),
                ),
                Err((status, error)) => match event_log.rollback_batch() {
                    Ok(()) => (status, serde_json::json!({"error": error})),
                    Err(rollback) => (
                        "503 Service Unavailable",
                        serde_json::json!({"error": format!("resume unavailable: {error}; rollback failed: {rollback}")}),
                    ),
                },
            };
            drop(write_guard);
            response
        }
        ("POST", path) if path.starts_with("/v1/missions/") && path.ends_with("/review") => {
            let Some(role) = operator_role(&request, principal) else {
                return write_http_response(
                    stream,
                    "401 Unauthorized",
                    serde_json::json!({"error": "operator review requires an authenticated role"}),
                )
                .await;
            };
            if role != RECOVERY_ADMIN_ROLE {
                return write_http_response(
                    stream,
                    "403 Forbidden",
                    serde_json::json!({"error": "operator role cannot acknowledge crash-loop quarantine"}),
                )
                .await;
            }
            let body: serde_json::Value = match serde_json::from_str(request_body) {
                Ok(body) => body,
                Err(_) => {
                    return write_http_response(
                        stream,
                        "400 Bad Request",
                        serde_json::json!({"error": "invalid review JSON"}),
                    )
                    .await;
                }
            };
            let generation = body
                .get("isolation_generation")
                .and_then(serde_json::Value::as_u64)
                .filter(|generation| *generation > 0);
            let Some(generation) = generation else {
                return write_http_response(
                    stream,
                    "400 Bad Request",
                    serde_json::json!({"error": "review requires one positive isolation_generation"}),
                )
                .await;
            };
            let reason = body
                .get("reason")
                .and_then(serde_json::Value::as_str)
                .filter(|reason| !reason.trim().is_empty() && reason.len() <= 512);
            let Some(reason) = reason else {
                return write_http_response(
                    stream,
                    "400 Bad Request",
                    serde_json::json!({"error": "review requires a bounded non-empty reason"}),
                )
                .await;
            };
            let mission = domain::MissionId::new(
                path.trim_start_matches("/v1/missions/")
                    .trim_end_matches("/review"),
            )?;
            let _write_guard = event_write_gate.lock()?;
            if let Err(error) = event_log.begin_batch() {
                drop(_write_guard);
                return write_http_response(stream,"503 Service Unavailable",serde_json::json!({"error": format!("review transaction unavailable: {error}")})).await;
            }
            let now = clock.now();
            let mut failure_status = "503 Service Unavailable";
            let result: Result<(), String> = (|| {
                let live = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned".to_string())?;
                let mut candidate = live.clone();
                drop(live);
                let correlation = domain::CorrelationId::new(format!("review-{mission}"))
                    .map_err(|error| error.to_string())?;
                let resume = resume_mission_candidate_with_policy(
                    &mut candidate,
                    &mission,
                    generation,
                    now,
                    &correlation,
                    &mut event_log.clone(),
                    true,
                    &role,
                    reason,
                );
                if let Some(error) = event_log.take_error().map_err(|error| error.to_string())? {
                    return Err(format!("review event sink failed: {error}"));
                }
                if let Err(error) = resume {
                    failure_status = if error.downcast_ref::<state::SqliteEventLogError>().is_some()
                        || error.downcast_ref::<serde_json::Error>().is_some()
                    {
                        "503 Service Unavailable"
                    } else {
                        "409 Conflict"
                    };
                    return Err(error.to_string());
                }
                let checkpoint = crate::diagnostics::http_checkpoint(&candidate)
                    .map_err(|error| error.to_string())?;
                crate::diagnostics::http_save(event_log, &checkpoint, &diagnostics)
                    .map_err(|error| error.to_string())?;
                crate::diagnostics::http_commit(event_log, &diagnostics)
                    .map_err(|error| error.to_string())?;
                let mut live = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned".to_string())?;
                *live = candidate;
                if let Err(error) = flush_unisolated_commands(&mut live, clock) {
                    eprintln!("durable reviewed outbox deferred: {error}");
                }
                Ok(())
            })();
            match result {
                Ok(()) => (
                    "200 OK",
                    serde_json::json!({
                        "status": "reviewed_and_resumed",
                        "mission_id": mission.as_str(),
                        "isolation_generation": generation,
                        "operator_role": role,
                    }),
                ),
                Err(error) => match event_log.rollback_batch() {
                    Ok(()) => (failure_status, serde_json::json!({"error": error})),
                    Err(rollback) => (
                        "503 Service Unavailable",
                        serde_json::json!({"error": format!("review unavailable: {error}; rollback failed: {rollback}")}),
                    ),
                },
            }
        }
        ("POST", path) if path.starts_with("/v1/missions/") && path.ends_with("/cancel") => {
            let mission_text = path
                .trim_start_matches("/v1/missions/")
                .trim_end_matches("/cancel")
                .trim_end_matches('/');
            let mission_id = domain::MissionId::new(mission_text)?;
            let gate_wait = StageObservation::start(
                &diagnostics,
                DiagnosticKind::Http,
                DiagnosticStage::WriteGate,
                None,
            );
            let _write_guard = event_write_gate.lock()?;
            drop(gate_wait);
            event_log.begin_batch()?;
            let now = clock.now();
            let mut pending_controller = None;
            let mut cancellation_status = "Cancelling".to_string();
            let result: Result<String, String> = {
                let controller = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let clone_sample = StageObservation::start(
                    controller.bridge.diagnostics(),
                    DiagnosticKind::Http,
                    DiagnosticStage::Clone,
                    None,
                );
                let mut candidate = controller.clone();
                drop(clone_sample);
                let mut events = event_log.clone();
                let operation: Result<(), String> = (|| {
                    let group_id = candidate
                        .orchestrator
                        .execution(&mission_id)
                        .ok_or_else(|| "unknown Mission cancellation".to_string())?
                        .group_id()
                        .clone();
                    application::safety_stop::admit_group_stop(
                        &mut candidate,
                        &group_id,
                        now,
                        10_000,
                    )?;
                    application::memory_use::revoke_group(
                        &mut candidate,
                        &group_id,
                        "Mission cancellation requested",
                        now,
                    )?;
                    let safety_blocked =
                        application::safety_stop::blocks_release(&candidate, &mission_id)
                            || application::memory_use::blocks_release(&candidate, &mission_id);
                    let ControllerState {
                        bridge,
                        orchestrator,
                        ..
                    } = &mut candidate;
                    orchestrator
                        .request_cancel(
                            &mission_id,
                            bridge.control_mut(),
                            now,
                            &domain::CorrelationId::new(format!("cancel-{mission_id}"))
                                .map_err(|error| error.to_string())?,
                            &mut events,
                        )
                        .map_err(|error| error.to_string())?;
                    let group_id = orchestrator
                        .execution(&mission_id)
                        .ok_or_else(|| "Mission disappeared during cancellation".to_string())?
                        .group_id()
                        .clone();
                    bridge
                        .request_group_cancellation(&group_id)
                        .map_err(|error| error.to_string())?;
                    if bridge.group_attempts_terminal(&group_id) && !safety_blocked {
                        orchestrator
                            .finalize_cancel(
                                &mission_id,
                                bridge.control_mut(),
                                now,
                                &domain::CorrelationId::new(format!(
                                    "cancel-finalize-{mission_id}"
                                ))
                                .map_err(|error| error.to_string())?,
                                &mut events,
                            )
                            .map_err(|error| error.to_string())?;
                    }
                    cancellation_status = format!(
                        "{:?}",
                        orchestrator
                            .execution(&mission_id)
                            .expect("cancellation retained Mission authority")
                            .lifecycle()
                    );
                    Ok(())
                })();
                if operation.is_ok() {
                    close_terminal_mission_coordination(&mut candidate, &mission_id);
                }
                operation
                    .and_then(|()| {
                        crate::diagnostics::http_checkpoint(&candidate)
                            .map_err(|error| error.to_string())
                    })
                    .inspect(|_| pending_controller = Some(candidate))
            };
            match result {
                Ok(checkpoint_json) => {
                    if let Err(error) =
                        crate::diagnostics::http_save(event_log, &checkpoint_json, &diagnostics)
                    {
                        let rollback = event_log.rollback_batch();
                        drop(_write_guard);
                        return write_http_response(
                            stream,
                            "503 Service Unavailable",
                            serde_json::json!({"error": format!("checkpoint persistence failed: {error}; rollback: {}", rollback.as_ref().err().map(ToString::to_string).unwrap_or_else(|| "ok".to_string()))}),
                        ).await;
                    }
                    if let Err(error) = crate::diagnostics::http_commit(event_log, &diagnostics) {
                        let rollback = event_log.rollback_batch();
                        drop(_write_guard);
                        return write_http_response(
                            stream,
                            "503 Service Unavailable",
                            serde_json::json!({"error": format!("event batch commit failed: {error}; rollback: {}", rollback.as_ref().err().map(ToString::to_string).unwrap_or_else(|| "ok".to_string()))}),
                        ).await;
                    }
                    if let Some(candidate) = pending_controller {
                        let mut live = controller
                            .lock()
                            .map_err(|_| "controller lock is poisoned")?;
                        *live = candidate;
                        if let Err(error) = flush_unisolated_commands(&mut live, clock) {
                            eprintln!("durable command outbox delivery deferred: {error}");
                        }
                    }
                    (
                        "202 Accepted",
                        serde_json::json!({"status": cancellation_status}),
                    )
                }
                Err(error) => {
                    event_log.rollback_batch()?;
                    drop(_write_guard);
                    ("409 Conflict", serde_json::json!({"error": error}))
                }
            }
        }
        ("GET", "/v1/scheduling-reservations") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            let reservations = controller
                .bridge
                .control()
                .scheduled_tasks()
                .map(|reservation| {
                    let decision = reservation.decision();
                    serde_json::json!({
                        "mission_id": decision.task_ref().mission_id(),
                        "task_id": decision.task_ref().task_id(),
                        "group_id": reservation.group_id(),
                        "phase": format!("{:?}", reservation.phase()),
                        "starts_at_ms": decision.starts_at().as_millis(),
                        "ends_at_ms": decision.ends_at().map(domain::TimestampMs::as_millis),
                        "latest_activation_at_ms": decision.latest_activation_at()
                            .map(domain::TimestampMs::as_millis),
                        "snapshot_version": decision.snapshot_version(),
                        "expansions": decision.expansions(),
                        "reason": reservation.reason(),
                        "selections": decision.selections().iter().map(|selection| {
                            serde_json::json!({
                                "role_id": selection.role_id(),
                                "node_id": selection.node_id(),
                                "resources": selection.resource_ids().iter()
                                    .zip(selection.resource_units())
                                    .map(|(resource_id, units)| serde_json::json!({
                                        "resource_id": resource_id,
                                        "units": units,
                                    }))
                                    .collect::<Vec<_>>(),
                            })
                        }).collect::<Vec<_>>(),
                    })
                })
                .collect::<Vec<_>>();
            (
                "200 OK",
                serde_json::json!({
                    "schema": "roboguide.scheduling-reservations/v0.1",
                    "calendar_version": controller.bridge.control().scheduling_snapshot(clock.now()).version(),
                    "reservations": reservations,
                }),
            )
        }
        ("GET", "/v1/execution-attempts") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            let attempts = controller
                .bridge
                .attempt_history()
                .into_iter()
                .map(|attempt| {
                    let command = attempt.command();
                    serde_json::json!({
                        "execution_id": attempt.execution_id(),
                        "mission_id": command.mission_id(),
                        "task_id": command.task_ref().task_id(),
                        "group_id": command.group_id(),
                        "role_id": command.role_id(),
                        "node_id": command.node_id(),
                        "status": format!("{:?}", attempt.status()),
                    })
                })
                .collect::<Vec<_>>();
            (
                "200 OK",
                serde_json::json!({
                    "schema": "roboguide.execution-attempt-history/v0.1",
                    "attempts": attempts,
                }),
            )
        }
        ("GET", path) if path.starts_with("/v1/executions/") && path.ends_with("/progress") => {
            let execution_id = path
                .trim_start_matches("/v1/executions/")
                .trim_end_matches("/progress");
            let parameters = parse_query(query);
            let stall_after_ms = parameters
                .get("stall_after_ms")
                .map(|value| value.parse::<u64>())
                .transpose();
            let Ok(stall_after_ms) = stall_after_ms else {
                return write_http_response(
                    stream,
                    "400 Bad Request",
                    serde_json::json!({"error": "invalid progress policy interval"}),
                )
                .await;
            };
            if stall_after_ms.is_some_and(|value| value == 0 || value > 86_400_000) {
                return write_http_response(
                    stream,
                    "400 Bad Request",
                    serde_json::json!({"error": "invalid progress policy interval"}),
                )
                .await;
            }
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            if controller.bridge.execution_status(execution_id).is_none() {
                (
                    "404 Not Found",
                    serde_json::json!({"error": "unknown execution"}),
                )
            } else {
                (
                    "200 OK",
                    serde_json::json!({
                            "schema_version": "roboguide.execution-progress-view/v0.1",
                            "execution_id": execution_id,
                        "disposition": controller.bridge.execution_progress(execution_id, clock.now(), stall_after_ms),
                    "sample": controller.bridge.execution_progress_sample(execution_id),
                    "evidence": controller.bridge.execution_progress_evidence(execution_id),
                            "stall_after_ms": stall_after_ms,
                            "observation_only": true,
                        }),
                )
            }
        }
        ("GET", path) if path.starts_with("/v1/groups/") && path.ends_with("/recovery") => {
            let group_id = match domain::ExecutionGroupId::new(
                path.trim_start_matches("/v1/groups/")
                    .trim_end_matches("/recovery")
                    .trim_end_matches('/'),
            ) {
                Ok(group_id) => group_id,
                Err(error) => {
                    return write_http_response(
                        stream,
                        "400 Bad Request",
                        serde_json::json!({"error": error.to_string()}),
                    )
                    .await;
                }
            };
            let live = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            if live.bridge.control().group(&group_id).is_none() {
                (
                    "404 Not Found",
                    serde_json::json!({"error": "unknown Group"}),
                )
            } else {
                (
                    "200 OK",
                    serde_json::json!({
                        "schema_version": "roboguide.group-recovery-view/v0.1",
                        "group_id": group_id,
                        "disposition": live.bridge.group_recovery_disposition(&group_id, clock.now()),
                        "stop_intent": live.bridge.group_recovery(&group_id),
                        "deployment_support_unchanged": live.bridge.group_recovery_support_unchanged(&group_id),
                        "continuation_admission_error": live.bridge.group_continuation_blocker(&group_id, clock.now()),
                        "resources_released_by_recovery": false,
                        "command_receipt_is_stop_proof": false,
                    }),
                )
            }
        }
        ("POST", path) if path.starts_with("/v1/groups/") && path.ends_with("/recover") => {
            let command = match super::recovery::GroupRecoveryCommand::parse(request_body) {
                Ok(command) => command,
                Err(error) => {
                    return write_http_response(
                        stream,
                        "400 Bad Request",
                        serde_json::json!({"error": error}),
                    )
                    .await;
                }
            };
            let group_id = match domain::ExecutionGroupId::new(
                path.trim_start_matches("/v1/groups/")
                    .trim_end_matches("/recover")
                    .trim_end_matches('/'),
            ) {
                Ok(group_id) => group_id,
                Err(error) => {
                    return write_http_response(
                        stream,
                        "400 Bad Request",
                        serde_json::json!({"error": error.to_string()}),
                    )
                    .await;
                }
            };
            let gate_wait = StageObservation::start(
                &diagnostics,
                DiagnosticKind::Http,
                DiagnosticStage::WriteGate,
                None,
            );
            let _write_guard = event_write_gate.lock()?;
            drop(gate_wait);
            event_log.begin_batch()?;
            let result: Result<(ControllerState, String), String> = (|| {
                let live = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let clone_sample = StageObservation::start(
                    live.bridge.diagnostics(),
                    DiagnosticKind::Http,
                    DiagnosticStage::Clone,
                    None,
                );
                let mut candidate = live.clone();
                drop(clone_sample);
                let mission_id = candidate
                    .bridge
                    .control()
                    .group(&group_id)
                    .ok_or_else(|| "unknown recovery Group".to_string())?
                    .mission_id()
                    .clone();
                if !candidate
                    .orchestrator
                    .execution(&mission_id)
                    .is_some_and(|mission| {
                        mission.lifecycle() == orchestration::MissionExecutionLifecycle::Running
                            && mission.isolation().is_none()
                    })
                {
                    return Err("only a running Mission can authorize Group recovery".into());
                }
                candidate
                    .bridge
                    .request_group_recovery(
                        &group_id,
                        &command.recovery_id,
                        command.members,
                        clock.now(),
                        command.timeout_ms,
                        command.max_replacements,
                    )
                    .map_err(|error| error.to_string())?;
                let checkpoint = crate::diagnostics::http_checkpoint(&candidate)
                    .map_err(|error| error.to_string())?;
                Ok((candidate, checkpoint))
            })();
            match result {
                Ok((candidate, checkpoint)) => {
                    if let Err(error) =
                        crate::diagnostics::http_save(event_log, &checkpoint, &diagnostics)
                    {
                        event_log.rollback_batch()?;
                        return Err(error.into());
                    }
                    if let Err(error) = crate::diagnostics::http_commit(event_log, &diagnostics) {
                        let _ = event_log.rollback_batch();
                        return Err(error.into());
                    }
                    let mut live = controller
                        .lock()
                        .map_err(|_| "controller lock is poisoned")?;
                    *live = candidate;
                    if let Err(error) = flush_unisolated_commands(&mut live, clock) {
                        eprintln!("durable Group command delivery deferred: {error}");
                    }
                    (
                        "202 Accepted",
                        serde_json::json!({"status": "group_recovery_stop_requested",
                        "group_id": group_id, "stop_intent": live.bridge.group_recovery(&group_id),
                        "resources_released_by_recovery": false, "command_receipt_is_stop_proof": false}),
                    )
                }
                Err(error) => {
                    event_log.rollback_batch()?;
                    ("409 Conflict", serde_json::json!({"error": error}))
                }
            }
        }
        ("GET", path) if path.starts_with("/v1/executions/") && path.ends_with("/recovery") => {
            let execution_id = path
                .trim_start_matches("/v1/executions/")
                .trim_end_matches("/recovery");
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            match controller.bridge.execution_status(execution_id) {
                Some(status) => (
                    "200 OK",
                    serde_json::json!({
                        "schema_version": "roboguide.execution-recovery-view/v0.2",
                        "execution_id": execution_id,
                        "execution_status": format!("{status:?}"),
                        "disposition": controller.bridge.execution_recovery_disposition(execution_id, clock.now()),
                        "stop_intent": controller.bridge.execution_recovery_stop(execution_id),
                        "deployment_support": controller.bridge.attempt_history().into_iter()
                            .find(|attempt| attempt.execution_id() == execution_id)
                            .map(|attempt| controller.bridge.recovery_deployment_support(attempt.command())),
                        "support_is_authorization": false,
                    }),
                ),
                None => (
                    "404 Not Found",
                    serde_json::json!({"error": "unknown execution"}),
                ),
            }
        }
        ("GET", path) if path.starts_with("/v1/executions/") => {
            let execution_id = path.trim_start_matches("/v1/executions/");
            let execution_id = execution_id.trim_end_matches("/");
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            match controller.bridge.execution_status(execution_id) {
                Some(status) => (
                    "200 OK",
                    serde_json::json!({"execution_id": execution_id, "status": format!("{status:?}")}),
                ),
                None => (
                    "404 Not Found",
                    serde_json::json!({"error": "unknown execution"}),
                ),
            }
        }
        ("POST", path)
            if path.starts_with("/v1/executions/")
                && (path.ends_with("/cancel") || path.ends_with("/recover")) =>
        {
            let recovery = if path.ends_with("/recover") {
                match super::recovery::RecoveryCommand::parse(request_body) {
                    Ok(command) => Some(command),
                    Err(error) => {
                        return write_http_response(
                            stream,
                            "400 Bad Request",
                            serde_json::json!({"error": error}),
                        )
                        .await;
                    }
                }
            } else {
                None
            };
            let execution_id = path
                .trim_start_matches("/v1/executions/")
                .trim_end_matches("/cancel")
                .trim_end_matches("/recover")
                .trim_end_matches('/')
                .to_string();
            let gate_wait = StageObservation::start(
                &diagnostics,
                DiagnosticKind::Http,
                DiagnosticStage::WriteGate,
                None,
            );
            let _write_guard = event_write_gate.lock()?;
            drop(gate_wait);
            event_log.begin_batch()?;
            let result: Result<(ControllerState, String), String> = (|| {
                let live = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let clone_sample = StageObservation::start(
                    live.bridge.diagnostics(),
                    DiagnosticKind::Http,
                    DiagnosticStage::Clone,
                    None,
                );
                let mut candidate = live.clone();
                drop(clone_sample);
                if let Some(command) = &recovery {
                    let mission_id = candidate
                        .bridge
                        .attempt_history()
                        .into_iter()
                        .find(|attempt| attempt.execution_id() == execution_id)
                        .map(|attempt| attempt.command().mission_id().clone())
                        .ok_or_else(|| "unknown recovery attempt".to_string())?;
                    if !candidate
                        .orchestrator
                        .execution(&mission_id)
                        .is_some_and(|mission| {
                            mission.lifecycle() == orchestration::MissionExecutionLifecycle::Running
                                && mission.isolation().is_none()
                        })
                    {
                        return Err("only a running Mission can authorize recovery".into());
                    }
                    let node = domain::NodeId::new(&command.expected_node_id)
                        .map_err(|error| error.to_string())?;
                    candidate
                        .bridge
                        .request_execution_recovery(
                            &execution_id,
                            &node,
                            clock.now(),
                            command.timeout_ms,
                            command.max_replacements,
                        )
                        .map_err(|error| error.to_string())?;
                } else {
                    let command = candidate
                        .bridge
                        .attempt_history()
                        .into_iter()
                        .find(|attempt| attempt.execution_id() == execution_id)
                        .map(|attempt| attempt.command().clone())
                        .ok_or_else(|| "unknown cancellation attempt".to_string())?;
                    application::safety_stop::admit(
                        &mut candidate,
                        command.group_id(),
                        command.task_ref(),
                        command.role_id(),
                        clock.now(),
                        10_000,
                    )?;
                    candidate
                        .bridge
                        .cancel(&execution_id)
                        .map_err(|error| error.to_string())?;
                }
                let checkpoint = crate::diagnostics::http_checkpoint(&candidate)
                    .map_err(|error| error.to_string())?;
                Ok((candidate, checkpoint))
            })();
            match result {
                Ok((candidate, checkpoint)) => {
                    if let Err(error) =
                        crate::diagnostics::http_save(event_log, &checkpoint, &diagnostics)
                    {
                        event_log.rollback_batch()?;
                        return Err(error.into());
                    }
                    if let Err(error) = crate::diagnostics::http_commit(event_log, &diagnostics) {
                        let _ = event_log.rollback_batch();
                        return Err(error.into());
                    }
                    let mut live = controller
                        .lock()
                        .map_err(|_| "controller lock is poisoned")?;
                    *live = candidate;
                    if let Err(error) = flush_unisolated_commands(&mut live, clock) {
                        eprintln!("durable command outbox delivery deferred: {error}");
                    }
                    (
                        "202 Accepted",
                        if recovery.is_some() {
                            serde_json::json!({
                                "status": "recovery_stop_requested",
                                "execution_id": execution_id,
                                "stop_intent": live.bridge.execution_recovery_stop(&execution_id),
                                "command_receipt_is_stop_proof": false,
                            })
                        } else {
                            serde_json::json!({"status": "cancel_requested"})
                        },
                    )
                }
                Err(error) => {
                    event_log.rollback_batch()?;
                    ("409 Conflict", serde_json::json!({"error": error}))
                }
            }
        }
        _ => ("404 Not Found", serde_json::json!({"error": "not found"})),
    };
    write_http_response(stream, status, body).await
}

/// Renders bounded process metrics without exposing instruction bodies or high-cardinality IDs.
fn controller_metrics(
    controller: &ControllerState,
    event_log: &state::SqliteEventLog,
    now: domain::TimestampMs,
) -> Result<String, String> {
    let missions = controller.orchestrator.mission_ids();
    let active_missions = missions
        .iter()
        .filter(|mission| {
            controller
                .orchestrator
                .execution(mission)
                .is_some_and(|execution| {
                    matches!(
                        execution.lifecycle(),
                        orchestration::MissionExecutionLifecycle::Accepted
                            | orchestration::MissionExecutionLifecycle::Running
                            | orchestration::MissionExecutionLifecycle::Cancelling
                    )
                })
        })
        .count();
    let nodes = controller.bridge.state().nodes();
    let unknown_attempts = controller.bridge.current_unknown_attempts().len();
    let pending_groups = controller
        .bridge
        .group_recovery_ids()
        .iter()
        .filter(|group| {
            matches!(
                controller.bridge.group_recovery_disposition(group, now),
                runtime::GroupRecoveryDisposition::AwaitingStop
                    | runtime::GroupRecoveryDisposition::StopConfirmed
                    | runtime::GroupRecoveryDisposition::ContinuationPrepared
            )
        })
        .count();
    let availability = event_log
        .write_status()
        .map_err(|error| error.to_string())?;
    let latest_sequence = event_log.latest_sequence();
    let journal_entries = event_log.control_command_count();
    let storage_read_available = u8::from(latest_sequence.is_ok() && journal_entries.is_ok());
    let latest_sequence = latest_sequence.map_or_else(|_| "NaN".into(), |value| value.to_string());
    let journal_entries = journal_entries.map_or_else(|_| "NaN".into(), |value| value.to_string());
    let writes_enabled = u8::from(availability.write_enabled);
    let disabled_generation = availability.generation;
    let health_online = nodes
        .iter()
        .filter(|node| node.reported_status().health().is_schedulable())
        .count();
    let (memory_unresolved, memory_queries, memory_exhausted) =
        application::memory_use_query::metrics(controller, now);
    let (handoffs_pending, handoffs_manual) = application::memory_handoff::metrics(controller);
    let (stop_pending, stop_manual, stop_overdue) =
        application::safety_stop::metrics(controller, now);
    let dispatch_pending = controller.bridge.pending_dispatch_intents().len();
    let mut output = format!(
        "# HELP roboguide_stop_purposes_pending Retained stop purposes without confirmed original evidence.\n\
         # TYPE roboguide_stop_purposes_pending gauge\n\
         roboguide_stop_purposes_pending {stop_pending}\n\
         # HELP roboguide_stop_purposes_manual Retained stop purposes requiring manual escalation.\n\
         # TYPE roboguide_stop_purposes_manual gauge\n\
         roboguide_stop_purposes_manual {stop_manual}\n\
         # HELP roboguide_stop_purposes_overdue Unconfirmed original stop deadlines already elapsed.\n\
         # TYPE roboguide_stop_purposes_overdue gauge\n\
         roboguide_stop_purposes_overdue {stop_overdue}\n\
         # HELP roboguide_execute_dispatch_intents_pending Actual pending durable Execute intents.\n\
         # TYPE roboguide_execute_dispatch_intents_pending gauge\n\
         roboguide_execute_dispatch_intents_pending {dispatch_pending}\n\
         # HELP roboguide_authority_write_enabled Whether this process admits durable authority writes.\n\
         # TYPE roboguide_authority_write_enabled gauge\n\
         roboguide_authority_write_enabled {writes_enabled}\n\
         # HELP roboguide_authority_disabled_generation Last process-local write failure generation.\n\
         # TYPE roboguide_authority_disabled_generation gauge\n\
         roboguide_authority_disabled_generation {disabled_generation}\n\
         # HELP roboguide_storage_read_available Whether SQL event and journal counts were readable.\n\
         # TYPE roboguide_storage_read_available gauge\n\
         roboguide_storage_read_available {storage_read_available}\n\
         # HELP roboguide_memory_handoffs_pending Exchanges awaiting original barriers or explicit commit.\n\
         # TYPE roboguide_memory_handoffs_pending gauge\n\
         roboguide_memory_handoffs_pending {handoffs_pending}\n\
         # HELP roboguide_memory_handoffs_manual Exchanges retaining ownership for manual reconciliation.\n\
         # TYPE roboguide_memory_handoffs_manual gauge\n\
         roboguide_memory_handoffs_manual {handoffs_manual}\n\
         # HELP roboguide_memory_revoke_providers_unresolved Original providers lacking revoke confirmation.\n\
         # TYPE roboguide_memory_revoke_providers_unresolved gauge\n\
         roboguide_memory_revoke_providers_unresolved {memory_unresolved}\n\
         # HELP roboguide_memory_use_queries_pending Pending original readonly observations.\n\
         # TYPE roboguide_memory_use_queries_pending gauge\n\
         roboguide_memory_use_queries_pending {memory_queries}\n\
         # HELP roboguide_memory_use_observations_exhausted Observation sessions past their frozen deadline.\n\
         # TYPE roboguide_memory_use_observations_exhausted gauge\n\
         roboguide_memory_use_observations_exhausted {memory_exhausted}\n\
         # HELP roboguide_missions_total Accepted Mission projections.\n\
         # TYPE roboguide_missions_total gauge\n\
         roboguide_missions_total {}\n\
         # HELP roboguide_missions_active Active Mission executions.\n\
         # TYPE roboguide_missions_active gauge\n\
         roboguide_missions_active {}\n\
         # HELP roboguide_nodes_total Registered Node projections.\n\
         # TYPE roboguide_nodes_total gauge\n\
         roboguide_nodes_total {}\n\
         # HELP roboguide_nodes_schedulable Nodes with schedulable reported health.\n\
         # TYPE roboguide_nodes_schedulable gauge\n\
         roboguide_nodes_schedulable {}\n\
         # HELP roboguide_runtime_unknown_attempts Physical attempts awaiting reconciliation.\n\
         # TYPE roboguide_runtime_unknown_attempts gauge\n\
         roboguide_runtime_unknown_attempts {}\n\
         # HELP roboguide_recovery_groups_pending Groups awaiting stop/continuation evidence.\n\
         # TYPE roboguide_recovery_groups_pending gauge\n\
         roboguide_recovery_groups_pending {}\n\
         # HELP roboguide_event_log_sequence Durable event high-water.\n\
         # TYPE roboguide_event_log_sequence gauge\n\
         roboguide_event_log_sequence {}\n\
         # HELP roboguide_control_journal_entries Durable Control command evidence rows.\n\
         # TYPE roboguide_control_journal_entries gauge\n\
         roboguide_control_journal_entries {}\n",
        missions.len(),
        active_missions,
        nodes.len(),
        health_online,
        unknown_attempts,
        pending_groups,
        latest_sequence,
        journal_entries,
    );
    output.push_str(&crate::observability::render(
        controller.bridge.diagnostics(),
    ));
    Ok(output)
}
