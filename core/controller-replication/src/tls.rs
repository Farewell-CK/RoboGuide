//! Small rustls loader shared by asynchronous consensus RPCs and synchronous child fences.

use crate::ReplicaTlsConfig;
use rustls::pki_types::{CertificateDer, PrivateKeyDer, ServerName};
use rustls::{ClientConfig, RootCertStore, ServerConfig};
use std::collections::BTreeMap;
use std::fs::File;
use std::io::BufReader;
use std::sync::Arc;

/// Loaded mutual-TLS configuration owned by one replica process.
#[derive(Clone)]
pub(crate) struct TlsMaterial {
    /// Client certificate verifier and trust roots.
    pub(crate) client: Arc<ClientConfig>,
    /// Server certificate and mandatory client certificate verifier.
    pub(crate) server: Arc<ServerConfig>,
    /// Startup-frozen DNS name used by clients when validating the peer certificate.
    pub(crate) server_name: String,
    /// Expected leaf certificate digest per infrastructure member.
    pub(crate) peer_certificate_digests: BTreeMap<u64, String>,
}

/// Loads one strict PEM file containing one or more certificates.
fn certificates(path: &std::path::Path) -> Result<Vec<CertificateDer<'static>>, String> {
    let mut reader = BufReader::new(File::open(path).map_err(|error| error.to_string())?);
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
    // The workspace also enables aws-lc through tonic; select ring explicitly for this profile.
    let _ = rustls::crypto::ring::default_provider().install_default();
    let ca = certificates(&config.ca_cert_path)?;
    let cert_chain = certificates(&config.cert_path)?;
    if ca.is_empty() || cert_chain.is_empty() {
        return Err("replica TLS CA/certificate chain must not be empty".into());
    }
    let key = private_key(&config.key_path)?;
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
    let verifier = rustls::server::WebPkiClientVerifier::builder(Arc::new(roots))
        .build()
        .map_err(|error| format!("replica TLS client verifier is invalid: {error}"))?;
    let server = ServerConfig::builder()
        .with_client_cert_verifier(verifier)
        .with_single_cert(cert_chain, key)
        .map_err(|error| format!("replica TLS server profile is invalid: {error}"))?;
    ServerName::try_from(config.server_name.clone())
        .map_err(|_| "replica TLS server_name must be a valid DNS name".to_string())?;
    Ok(Some(TlsMaterial {
        client: Arc::new(client),
        server: Arc::new(server),
        server_name: config.server_name.clone(),
        peer_certificate_digests: config.peer_certificate_digests.clone(),
    }))
}
