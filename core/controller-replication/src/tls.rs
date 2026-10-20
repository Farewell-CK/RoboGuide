//! Small rustls loader shared by asynchronous consensus RPCs and synchronous child fences.

use crate::{CertificateTrust, ReplicaTlsConfig, SqliteRaftStore};
use rustls::client::danger::ServerCertVerifier as _;
use rustls::pki_types::{CertificateDer, PrivateKeyDer, ServerName};
use rustls::{ClientConfig, RootCertStore, ServerConfig};
use std::collections::BTreeMap;
use std::fs::File;
use std::io::BufReader;
use std::sync::Arc;

/// Loaded mutual-TLS configuration owned by one replica process.
#[derive(Clone)]
pub(crate) struct TlsMaterial {
    /// Canonical identity of the actual CA DER set loaded with these connection credentials.
    pub(crate) ca_bundle_digest: String,
    /// Leaf identity of this process, checked against its configured infrastructure member.
    pub(crate) local_certificate_digest: String,
    /// Client certificate verifier and trust roots.
    pub(crate) client: Arc<ClientConfig>,
    /// Server certificate and mandatory client certificate verifier.
    pub(crate) server: Arc<ServerConfig>,
    /// Startup-frozen DNS name used by clients when validating the peer certificate.
    pub(crate) server_name: String,
    /// Expected leaf certificate digest per infrastructure member.
    pub(crate) peer_certificate_digests: BTreeMap<u64, String>,
    /// Original authority and atomic credential bundle for explicit v0.3 connection refresh.
    dynamic: Option<Arc<DynamicIdentity>>,
    /// Actual local chain used by this connection, retained across file replacement.
    local_chain: Arc<Vec<CertificateDer<'static>>>,
    /// Rechecks incoming client chain validity after waiting for the member writer.
    client_verifier: Arc<dyn rustls::server::danger::ClientCertVerifier>,
    /// Rechecks actual server chain validity immediately before application transmission.
    server_verifier: Arc<rustls::client::WebPkiServerVerifier>,
}

/// Exact connection evidence; file replacement cannot replace the certificates actually presented.
pub(crate) struct VerifiedTlsConnection {
    /// Actual served credential and its original current authority source.
    pub(crate) local: TlsMaterial,
    /// Full actual peer chain from the successful original mutual-TLS handshake.
    pub(crate) peer_chain: Vec<CertificateDer<'static>>,
}

impl VerifiedTlsConnection {
    /// Hashes only the actual TLS peer leaf, never a request field or later credential file.
    pub(crate) fn peer_digest(&self) -> Option<String> {
        use sha2::{Digest as _, Sha256};
        self.peer_chain
            .first()
            .map(|leaf| format!("sha256:{:x}", Sha256::digest(leaf.as_ref())))
    }

    /// Requires still-valid actual chains and current exact identities under the mutation writer.
    pub(crate) fn authorize(&self, sender: u64, local_member: u64) -> Result<bool, String> {
        self.authorize_at(sender, local_member, rustls::pki_types::UnixTime::now())
    }

    /// Verifies exact captured chains at one explicit PKI instant; tests never alter State clocks.
    pub(crate) fn authorize_at(
        &self,
        sender: u64,
        local_member: u64,
        now: rustls::pki_types::UnixTime,
    ) -> Result<bool, String> {
        let peer = self
            .peer_chain
            .first()
            .ok_or("TLS connection has no actual peer certificate")?;
        self.local
            .client_verifier
            .verify_client_cert(peer, &self.peer_chain[1..], now)
            .map_err(|error| format!("actual peer certificate is no longer valid: {error}"))?;
        self.local.verify_server_at(&self.local.local_chain, now)?;
        let peer_digest = self
            .peer_digest()
            .ok_or("actual peer leaf digest is absent")?;
        Ok(self.local.admits_digest(sender, &peer_digest)?
            && self
                .local
                .admits_digest(local_member, &self.local.local_certificate_digest)?)
    }
}

/// Original committed source; neither child nor administration readers become a second writer.
#[derive(Clone)]
enum TrustSource {
    /// Live replica store retains its actual original writer lease.
    Store(SqliteRaftStore),
    /// Child or administration process reads the original database including its live WAL.
    Readonly {
        /// Deployment-owned original replica database path.
        path: std::path::PathBuf,
        /// Exact original cluster profile required before consuming trust.
        profile: String,
    },
}

/// Credentials are transport material; current authority always comes from the committed source.
struct DynamicIdentity {
    /// Immutable original paths and bootstrap pins.
    config: ReplicaTlsConfig,
    /// Stable local infrastructure identity.
    member: u64,
    /// Exact original source, never a fallback to old pins on failure.
    source: TrustSource,
}

impl DynamicIdentity {
    /// Requires the original current complete trust projection on every authority check.
    fn trust(&self) -> Result<CertificateTrust, String> {
        match &self.source {
            TrustSource::Store(store) => store
                .certificate_trust()?
                .ok_or("original certificate authority is missing".into()),
            TrustSource::Readonly { path, profile } => SqliteRaftStore::readonly_certificate_trust(
                path,
                profile,
                &self.config.peer_certificate_digests,
            ),
        }
    }
}

impl TlsMaterial {
    /// Uses the normal rustls verifier again at current PKI time without granting member authority.
    fn verify_server_now(&self, chain: &[CertificateDer<'_>]) -> Result<(), String> {
        self.verify_server_at(chain, rustls::pki_types::UnixTime::now())
    }

    /// Retains the actual presented chain when checking the caller-owned PKI instant.
    fn verify_server_at(
        &self,
        chain: &[CertificateDer<'_>],
        now: rustls::pki_types::UnixTime,
    ) -> Result<(), String> {
        let leaf = chain.first().ok_or("TLS server chain is empty")?;
        let name =
            ServerName::try_from(self.server_name.clone()).map_err(|error| error.to_string())?;
        self.server_verifier
            .verify_server_cert(leaf, &chain[1..], &name, &[], now)
            .map(|_| ())
            .map_err(|error| format!("actual server certificate is no longer valid: {error}"))
    }
    /// Reads a single atomic PEM bundle before each real connection; legacy credentials stay fixed.
    pub(crate) fn refreshed(&self) -> Result<Self, String> {
        let Some(dynamic) = &self.dynamic else {
            return Ok(self.clone());
        };
        let mut current = load_bundle(&dynamic.config)?;
        current.dynamic = Some(dynamic.clone());
        current.authenticate_local(dynamic.member)?;
        Ok(current)
    }

    /// Rechecks the current committed member/leaf after framing and before dispatch or transmission.
    pub(crate) fn admits_digest(&self, member: u64, leaf: &str) -> Result<bool, String> {
        match &self.dynamic {
            Some(dynamic) => Ok(dynamic.trust()?.admits(member, leaf)),
            None => Ok(self
                .peer_certificate_digests
                .get(&member)
                .is_some_and(|expected| expected == leaf)),
        }
    }

    /// Checks the exact target member after CA/DNS validation and before any application bytes flow.
    pub(crate) fn authenticate_peer(
        &self,
        member: u64,
        certificates: Option<&[CertificateDer<'_>]>,
    ) -> Result<(), String> {
        use sha2::{Digest as _, Sha256};
        self.verify_server_now(certificates.ok_or("TLS peer chain is absent")?)?;
        let actual = certificates
            .and_then(|certificates| certificates.first())
            .map(|certificate| format!("sha256:{:x}", Sha256::digest(certificate.as_ref())));
        if let Some(dynamic) = &self.dynamic {
            self.authenticate_local(dynamic.member)?;
        }
        let admitted = match actual.as_deref() {
            Some(leaf) => self.admits_digest(member, leaf)?,
            None => false,
        };
        if admitted {
            Ok(())
        } else {
            Err("replica TLS peer certificate differs from exact target member".into())
        }
    }

    /// Prevents a misconfigured local certificate from impersonating another member at startup.
    pub(crate) fn authenticate_local(&self, member: u64) -> Result<(), String> {
        if self.dynamic.is_some() {
            self.verify_server_now(&self.local_chain)?;
        }
        if self.admits_digest(member, &self.local_certificate_digest)? {
            Ok(())
        } else {
            Err("replica TLS local certificate differs from configured member".into())
        }
    }
}

/// Loads one strict PEM file containing one or more certificates.
fn certificates(path: &std::path::Path) -> Result<Vec<CertificateDer<'static>>, String> {
    use std::io::Read as _;
    let mut bytes = Vec::new();
    File::open(path)
        .map_err(|error| error.to_string())?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() > 1024 * 1024 {
        return Err("TLS certificate bundle exceeds bounds".into());
    }
    let mut reader = BufReader::new(&bytes[..]);
    rustls_pemfile::certs(&mut reader)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("cannot parse certificate {}: {error}", path.display()))
}

/// Loads the first supported private key from a PEM file.
fn private_key(path: &std::path::Path) -> Result<PrivateKeyDer<'static>, String> {
    let mut reader = BufReader::new(File::open(path).map_err(|error| error.to_string())?);
    rustls_pemfile::private_key(&mut reader)
        .map_err(|error| format!("cannot parse private key {}: {error}", path.display()))?
        .ok_or_else(|| format!("private key {} is empty", path.display()))
}

/// Loads and validates a complete mTLS profile before any listener or consensus engine starts.
pub(crate) fn load(config: Option<&ReplicaTlsConfig>) -> Result<Option<TlsMaterial>, String> {
    let Some(config) = config else {
        return Ok(None);
    };
    material(
        config,
        certificates(&config.cert_path)?,
        private_key(&config.key_path)?,
    )
    .map(Some)
}

/// Reads certificate and private key from the same bounded original inode to permit atomic rotation.
fn load_bundle(config: &ReplicaTlsConfig) -> Result<TlsMaterial, String> {
    use std::io::Read as _;
    if config.cert_path != config.key_path {
        return Err("rotating identity requires one atomic certificate/key PEM bundle".into());
    }
    let mut bytes = Vec::new();
    File::open(&config.cert_path)
        .map_err(|error| error.to_string())?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() > 1024 * 1024 {
        return Err("credential bundle exceeds bounds".into());
    }
    let chain = rustls_pemfile::certs(&mut &bytes[..])
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    let key = rustls_pemfile::private_key(&mut &bytes[..])
        .map_err(|error| error.to_string())?
        .ok_or("credential bundle has no private key")?;
    material(config, chain, key)
}

/// Builds fresh mandatory mutual TLS; exact member pins remain independently enforced.
fn material(
    config: &ReplicaTlsConfig,
    cert_chain: Vec<CertificateDer<'static>>,
    key: PrivateKeyDer<'static>,
) -> Result<TlsMaterial, String> {
    // The workspace also enables aws-lc through tonic; select ring explicitly for this profile.
    let _ = rustls::crypto::ring::default_provider().install_default();
    let ca = certificates(&config.ca_cert_path)?;
    if ca.is_empty() || cert_chain.is_empty() {
        return Err("replica TLS CA/certificate chain must not be empty".into());
    }
    use sha2::{Digest as _, Sha256};
    let local_certificate_digest = format!("sha256:{:x}", Sha256::digest(cert_chain[0].as_ref()));
    let mut roots_der = ca
        .iter()
        .map(|certificate| certificate.as_ref())
        .collect::<Vec<_>>();
    roots_der.sort_unstable();
    roots_der.dedup();
    let mut root_digest = Sha256::new();
    for der in roots_der {
        root_digest.update((der.len() as u64).to_be_bytes());
        root_digest.update(der);
    }
    let ca_bundle_digest = format!("sha256:{:x}", root_digest.finalize());
    let mut roots = RootCertStore::empty();
    for certificate in ca {
        roots
            .add(certificate)
            .map_err(|error| format!("replica TLS CA is invalid: {error}"))?;
    }
    let client = ClientConfig::builder()
        .with_root_certificates(roots.clone())
        .with_client_auth_cert(cert_chain.clone(), key.clone_key())
        .map_err(|error| format!("replica TLS client profile is invalid: {error}"))?;
    let roots = Arc::new(roots);
    let verifier = rustls::server::WebPkiClientVerifier::builder(roots.clone())
        .build()
        .map_err(|error| format!("replica TLS client verifier is invalid: {error}"))?;
    let server_verifier = rustls::client::WebPkiServerVerifier::builder(roots)
        .build()
        .map_err(|error| error.to_string())?;
    let local_chain = Arc::new(cert_chain.clone());
    let server = ServerConfig::builder()
        .with_client_cert_verifier(verifier.clone())
        .with_single_cert(cert_chain, key)
        .map_err(|error| format!("replica TLS server profile is invalid: {error}"))?;
    ServerName::try_from(config.server_name.clone())
        .map_err(|_| "replica TLS server_name must be a valid DNS name".to_string())?;
    Ok(TlsMaterial {
        ca_bundle_digest,
        local_certificate_digest,
        client: Arc::new(client),
        server: Arc::new(server),
        server_name: config.server_name.clone(),
        peer_certificate_digests: config.peer_certificate_digests.clone(),
        dynamic: None,
        local_chain,
        client_verifier: verifier,
        server_verifier,
    })
}

/// Shares live committed trust with the replica listener and all fresh Raft client handshakes.
pub(crate) fn with_store_trust(
    config: &ReplicaTlsConfig,
    store: SqliteRaftStore,
    member: u64,
) -> Result<TlsMaterial, String> {
    dynamic(config, TrustSource::Store(store), member)
}

/// Binds a read-only current trust source for administration and the owned Controller child.
pub(crate) fn with_readonly_trust(
    config: &ReplicaTlsConfig,
    path: &std::path::Path,
    profile: &str,
    member: u64,
) -> Result<TlsMaterial, String> {
    dynamic(
        config,
        TrustSource::Readonly {
            path: path.to_owned(),
            profile: profile.to_owned(),
        },
        member,
    )
}

/// Validates the original authority and actual current local bundle before enabling dynamic traffic.
fn dynamic(
    config: &ReplicaTlsConfig,
    source: TrustSource,
    member: u64,
) -> Result<TlsMaterial, String> {
    let mut tls = load_bundle(config)?;
    tls.dynamic = Some(Arc::new(DynamicIdentity {
        config: config.clone(),
        member,
        source,
    }));
    tls.authenticate_local(member)?;
    Ok(tls)
}
