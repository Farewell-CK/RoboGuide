//! Temporary offline PKI for adapter/system TLS tests; never a production trust source.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Paths to one short-lived client/server certificate and its temporary private key.
pub struct TestCertificate {
    /// PEM certificate signed by this fixture's CA.
    pub cert_path: PathBuf,
    /// Private key generated solely inside the caller-owned temporary directory.
    pub key_path: PathBuf,
}

/// Offline OpenSSL fixture; callers own the temporary directory and every spawned command.
pub struct TestPki {
    /// Independent short-lived CA certificate.
    pub ca_cert_path: PathBuf,
    /// Distinct identities with a common localhost DNS name to test exact leaf binding.
    pub identities: BTreeMap<String, TestCertificate>,
}

/// Runs one owned fixture-generation command without exposing key contents in diagnostics.
fn openssl(root: &Path, args: &[&str]) -> Result<(), String> {
    let result = std::process::Command::new("openssl")
        .args(args)
        .current_dir(root)
        .output()
        .map_err(|error| format!("offline TLS system tests require OpenSSL: {error}"))?;
    if result.status.success() {
        Ok(())
    } else {
        Err(format!(
            "test certificate generation failed: {}",
            String::from_utf8_lossy(&result.stderr)
        ))
    }
}

impl TestPki {
    /// Generates a private CA and distinct localhost leaves in a caller-owned fresh directory.
    pub fn new(root: &Path, identities: &[&str]) -> Result<Self, String> {
        std::fs::create_dir_all(root).map_err(|error| error.to_string())?;
        openssl(
            root,
            &[
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                "ca.key",
                "-out",
                "ca.crt",
                "-days",
                "2",
                "-subj",
                "/CN=RoboGuide offline fixture CA",
            ],
        )?;
        std::fs::write(root.join("leaf.ext"),"subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth,clientAuth\nbasicConstraints=CA:FALSE\n").map_err(|error|error.to_string())?;
        let mut leaves = BTreeMap::new();
        for identity in identities {
            if identity.is_empty()
                || identity.len() > 32
                || !identity
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
            {
                return Err("invalid test certificate identity".into());
            }
            let key = format!("{identity}.key");
            let csr = format!("{identity}.csr");
            let cert = format!("{identity}.crt");
            openssl(
                root,
                &[
                    "req",
                    "-new",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    &key,
                    "-out",
                    &csr,
                    "-subj",
                    "/CN=localhost",
                ],
            )?;
            openssl(
                root,
                &[
                    "x509",
                    "-req",
                    "-in",
                    &csr,
                    "-CA",
                    "ca.crt",
                    "-CAkey",
                    "ca.key",
                    "-CAcreateserial",
                    "-out",
                    &cert,
                    "-days",
                    "2",
                    "-extfile",
                    "leaf.ext",
                ],
            )?;
            if leaves
                .insert(
                    identity.to_string(),
                    TestCertificate {
                        cert_path: root.join(cert),
                        key_path: root.join(key),
                    },
                )
                .is_some()
            {
                return Err("duplicate test certificate identity".into());
            }
        }
        Ok(Self {
            ca_cert_path: root.join("ca.crt"),
            identities: leaves,
        })
    }
}
