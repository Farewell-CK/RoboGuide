//! Bounded owned mTLS peer I/O; no Controller mutex or application writer follows a network wait.

use super::*;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::time::Duration;

/// Explicit actual endpoint and leaf identity; no DNS redirects or ambient proxy credentials.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Peer {
    /// Actual configured address on this participant's declared service.
    pub(crate) address: SocketAddr,
    /// Actual TLS name, independently checked beside the exact leaf pin.
    pub(crate) server_name: String,
    /// Exact actual permitted server leaf.
    pub(crate) server_leaf: String,
    /// Explicit local trust file; never a system-wide permission wildcard.
    pub(crate) ca_file: String,
    /// Original CA bytes; configured filename alone grants no replacement authority.
    pub(crate) ca_digest: String,
    /// Explicit actual client identity file.
    pub(crate) client_cert: String,
    /// Exact original service leaf, independently checked before connecting.
    pub(crate) client_leaf: String,
    /// Local key file path only; no private material enters checkpoints or diagnostics.
    pub(crate) client_key: String,
    /// Actual enrolled stable service principal, also checked by every receiving participant.
    pub(crate) principal_id: String,
}

/// Reads a bounded explicit local credential input without logging its contents.
fn file(path: &str) -> Result<Vec<u8>, String> {
    let source = std::fs::File::open(Path::new(path)).map_err(|error| error.to_string())?;
    let mut bytes = Vec::new();
    source
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.is_empty() || bytes.len() > 1024 * 1024 {
        return Err("Federation credential input exceeds its bound".into());
    }
    Ok(bytes)
}

/// Constructs a fresh verified TLS identity from exact declared credential bytes.
fn tls(peer: &Peer) -> Result<Arc<rustls::ClientConfig>, String> {
    let ca = file(&peer.ca_file)?;
    if format!("sha256:{:x}", Sha256::digest(&ca)) != peer.ca_digest {
        return Err("Federation original CA bytes changed".into());
    }
    let mut roots = rustls::RootCertStore::empty();
    for certificate in rustls_pemfile::certs(&mut std::io::Cursor::new(ca)) {
        roots
            .add(certificate.map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
    }
    let cert = rustls_pemfile::certs(&mut std::io::Cursor::new(file(&peer.client_cert)?))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    if cert.first().is_none_or(|value| {
        format!("sha256:{:x}", Sha256::digest(value.as_ref())) != peer.client_leaf
    }) {
        return Err("Federation actual client leaf differs".into());
    }
    let key = rustls_pemfile::private_key(&mut std::io::Cursor::new(file(&peer.client_key)?))
        .map_err(|error| error.to_string())?
        .ok_or("Federation private identity unavailable")?;
    Ok(Arc::new(
        rustls::ClientConfig::builder()
            .with_root_certificates(roots)
            .with_client_auth_cert(cert, key)
            .map_err(|error| error.to_string())?,
    ))
}

impl Peer {
    /// Rejects ambiguous identities and actually verifies local credential bytes before admission.
    pub(crate) fn validate(&self) -> Result<(), String> {
        if self.server_name.is_empty()
            || self.server_name.len() > 253
            || self.principal_id.is_empty()
            || self.principal_id.len() > 128
            || [&self.server_leaf, &self.client_leaf, &self.ca_digest]
                .iter()
                .any(|value| !super::policy::valid_digest(value))
        {
            return Err("invalid explicit Federation peer identity".into());
        }
        tls(self)?;
        Ok(())
    }
}

/// Complete actual transport observation; a failed request never becomes proof of absent effects.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Reply {
    /// Actual authenticated HTTP status, never inferred from a JSON passed flag.
    pub(crate) status: u16,
    /// Complete bounded response body from the pinned real peer.
    pub(crate) body: serde_json::Value,
    /// Actual verified peer leaf, retained with the original response.
    pub(crate) leaf: String,
}

/// Sends one original effect or a read-only observation with one strict total local transport budget.
pub(crate) fn request(
    peer: &Peer,
    tenant: &str,
    path: &str,
    body: Option<&serde_json::Value>,
    budget_ms: u64,
) -> Result<Reply, String> {
    if budget_ms == 0
        || budget_ms > 10_000
        || !path.starts_with("/v1/federation/")
        || path.bytes().any(|value| matches!(value, b'\r' | b'\n'))
        || tenant.bytes().any(|value| value.is_ascii_control())
    {
        return Err("invalid bounded Federation transport request".into());
    }
    let started = std::time::Instant::now();
    let budget = Duration::from_millis(budget_ms);
    let config = tls(peer)?;
    let name = rustls::pki_types::ServerName::try_from(peer.server_name.clone())
        .map_err(|error| error.to_string())?;
    let mut socket =
        TcpStream::connect_timeout(&peer.address, budget.saturating_sub(started.elapsed()))
            .map_err(|error| error.to_string())?;
    socket
        .set_nodelay(true)
        .map_err(|error| error.to_string())?;
    let mut connection =
        rustls::ClientConnection::new(config, name).map_err(|error| error.to_string())?;
    while connection.is_handshaking() {
        timeouts(&socket, started, budget)?;
        connection
            .complete_io(&mut socket)
            .map_err(|error| error.to_string())?;
    }
    let leaf = connection
        .peer_certificates()
        .and_then(|chain| chain.first())
        .ok_or("actual Federation peer leaf unavailable")?;
    let leaf = format!("sha256:{:x}", Sha256::digest(leaf.as_ref()));
    if leaf != peer.server_leaf {
        return Err("actual Federation peer leaf pin refused".into());
    }
    let encoded = body
        .map(serde_json::to_vec)
        .transpose()
        .map_err(|error| error.to_string())?
        .unwrap_or_default();
    if encoded.len() > 3 * 1024 * 1024 {
        return Err("Federation original request exceeds bound".into());
    }
    let mut stream = rustls::StreamOwned::new(connection, socket);
    let header = format!(
        "{} {} HTTP/1.1\r\nHost: {}\r\nX-RoboGuide-Tenant: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        if body.is_some() { "POST" } else { "GET" },
        path,
        peer.address,
        tenant,
        encoded.len()
    );
    for chunk in [header.as_bytes(), &encoded] {
        timeouts(&stream.sock, started, budget)?;
        stream.write_all(chunk).map_err(|error| error.to_string())?;
    }
    let mut response = Vec::new();
    let mut buffer = [0_u8; 8192];
    loop {
        timeouts(&stream.sock, started, budget)?;
        match stream.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => response.extend_from_slice(&buffer[..count]),
            Err(error)
                if error.kind() == std::io::ErrorKind::UnexpectedEof
                    && response.windows(4).any(|value| value == b"\r\n\r\n") =>
            {
                break;
            }
            Err(error) => return Err(error.to_string()),
        }
        if response.len() > 4 * 1024 * 1024 {
            return Err("actual Federation response exceeds bound".into());
        }
    }
    let split = response
        .windows(4)
        .position(|value| value == b"\r\n\r\n")
        .ok_or("incomplete actual Federation HTTP response")?;
    if split > 32768 {
        return Err("Federation HTTP headers exceed bound".into());
    }
    let header = std::str::from_utf8(&response[..split]).map_err(|error| error.to_string())?;
    let status = header
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .ok_or("actual HTTP status absent")?
        .parse::<u16>()
        .map_err(|error| error.to_string())?;
    let lengths = header
        .lines()
        .filter_map(|line| line.split_once(':'))
        .filter(|(name, _)| name.eq_ignore_ascii_case("content-length"))
        .map(|(_, value)| value.trim().parse::<usize>())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    let bytes = &response[split + 4..];
    if lengths != vec![bytes.len()]
        || header
            .lines()
            .any(|line| line.to_ascii_lowercase().starts_with("transfer-encoding:"))
    {
        return Err("actual Federation HTTP framing differs".into());
    }
    Ok(Reply {
        status,
        body: serde_json::from_slice(bytes).map_err(|error| error.to_string())?,
        leaf,
    })
}

/// Reapplies the shrinking original total bound before every blocking socket operation.
fn timeouts(
    socket: &TcpStream,
    started: std::time::Instant,
    budget: Duration,
) -> Result<(), String> {
    let remaining = budget
        .checked_sub(started.elapsed())
        .filter(|value| !value.is_zero())
        .ok_or("original Federation transport budget expired")?;
    socket
        .set_read_timeout(Some(remaining))
        .and_then(|_| socket.set_write_timeout(Some(remaining)))
        .map_err(|error| error.to_string())
}
