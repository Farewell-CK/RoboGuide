//! Optional loopback-only observations of this supervisor's own fresh atomic status file.

use crate::supervisor::Status;
use controller_replication::ReplicaTransportSnapshot;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

/// Reads the local clock only for status observation, never replica acceptance or lease authority.
pub(super) fn unix_ms() -> Result<u64, String> {
    u64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| error.to_string())?
            .as_millis(),
    )
    .map_err(|error| error.to_string())
}

/// Binds an explicitly configured private route before service startup; no route is implicit.
pub(super) async fn start(
    address: Option<SocketAddr>,
    status: PathBuf,
) -> Result<Option<tokio::task::JoinHandle<()>>, String> {
    let Some(address) = address else {
        return Ok(None);
    };
    if !address.ip().is_loopback() || address.port() == 0 {
        return Err("replica metrics requires private loopback".into());
    }
    let listener = tokio::net::TcpListener::bind(address)
        .await
        .map_err(|error| error.to_string())?;
    Ok(Some(tokio::spawn(async move {
        let permits = std::sync::Arc::new(tokio::sync::Semaphore::new(16));
        loop {
            let Ok((mut stream, _)) = listener.accept().await else {
                return;
            };
            let Ok(permit) = permits.clone().try_acquire_owned() else {
                continue;
            };
            let path = status.clone();
            tokio::spawn(async move {
                let _permit = permit;
                let _ =
                    tokio::time::timeout(Duration::from_secs(2), serve(&mut stream, path)).await;
            });
        }
    })))
}

/// Serves one bounded GET and fresh original status; missing/stale data never becomes a healthy zero.
async fn serve(stream: &mut tokio::net::TcpStream, path: PathBuf) -> Result<(), String> {
    let mut bytes = Vec::new();
    let mut chunk = [0; 1024];
    while !bytes.windows(4).any(|value| value == b"\r\n\r\n") {
        let count = stream
            .read(&mut chunk)
            .await
            .map_err(|error| error.to_string())?;
        if count == 0 || bytes.len() + count > 4096 {
            return Err("invalid metrics request".into());
        }
        bytes.extend_from_slice(&chunk[..count]);
    }
    if !bytes.starts_with(b"GET /metrics HTTP/1.1\r\n") {
        return Err("unsupported metrics route".into());
    }
    let result: Result<String, String> = async {
        let metadata = tokio::fs::symlink_metadata(&path).await.map_err(|error| error.to_string())?;
        if !metadata.is_file() || metadata.len() > 64 * 1024 { return Err("invalid metrics status file".into()); }
        let mut bytes = Vec::new();
        tokio::fs::File::open(path).await.map_err(|error| error.to_string())?.take(64 * 1024 + 1).read_to_end(&mut bytes).await.map_err(|error| error.to_string())?;
        if bytes.len() > 64 * 1024 { return Err("metrics status file exceeds bound".into()); }
        let status: Status = serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
        let age = unix_ms()?.checked_sub(status.observed_unix_ms).ok_or("metrics status clock moved backwards")?;
        if age > 5000 { return Err("replica status is stale".into()); }
        Ok(format!("# HELP roboguide_replica_status_age_seconds Age of this supervisor's actual atomic status.\n# TYPE roboguide_replica_status_age_seconds gauge\nroboguide_replica_status_age_seconds {}\n# HELP roboguide_replica_quorum_epoch_present Whether this supervisor last confirmed a current majority epoch.\n# TYPE roboguide_replica_quorum_epoch_present gauge\nroboguide_replica_quorum_epoch_present {}\n# HELP roboguide_replica_owned_controller Whether this supervisor currently owns a Controller child.\n# TYPE roboguide_replica_owned_controller gauge\nroboguide_replica_owned_controller {}\n# HELP roboguide_replica_accepted_revision Actual accepted local authority revision.\n# TYPE roboguide_replica_accepted_revision gauge\nroboguide_replica_accepted_revision {}\n", age as f64 / 1000.0, u8::from(status.epoch.is_some()), u8::from(status.child_pid.is_some()), status.revision) + &transport_metrics(status.transport.as_ref()))
    }.await;
    let (status, body) = match result {
        Ok(body) => ("200 OK", body),
        Err(_) => (
            "503 Service Unavailable",
            "replica status unavailable\n".into(),
        ),
    };
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: text/plain; version=0.0.4\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    stream
        .write_all(response.as_bytes())
        .await
        .map_err(|error| error.to_string())?;
    stream.shutdown().await.map_err(|error| error.to_string())
}

/// Renders fixed-label actual local transport observations, keeping legacy absence unavailable.
fn transport_metrics(snapshot: Option<&ReplicaTransportSnapshot>) -> String {
    use std::fmt::Write as _;
    let mut output = format!(
        "# HELP roboguide_replica_transport_observation_present Actual local transport counters are available.\n# TYPE roboguide_replica_transport_observation_present gauge\nroboguide_replica_transport_observation_present {}\n",
        u8::from(snapshot.is_some())
    );
    let Some(snapshot) = snapshot else {
        return output;
    };
    for (name, kind, value) in [
        ("inbound_active", "gauge", snapshot.inbound_active as u64),
        ("outbound_active", "gauge", snapshot.outbound_active as u64),
        (
            "inbound_limit",
            "gauge",
            snapshot.limits.inbound_connections as u64,
        ),
        (
            "outbound_limit",
            "gauge",
            snapshot.limits.outbound_connections as u64,
        ),
        (
            "small_frame_active_bytes",
            "gauge",
            snapshot.small_frame_active_bytes as u64,
        ),
        (
            "large_frame_active_bytes",
            "gauge",
            snapshot.large_frame_active_bytes as u64,
        ),
        (
            "small_frame_limit_bytes",
            "gauge",
            u64::from(snapshot.limits.small_frame_bytes),
        ),
        (
            "large_frame_limit_bytes",
            "gauge",
            u64::from(snapshot.limits.large_frame_bytes),
        ),
        (
            "inbound_high_water",
            "gauge",
            snapshot.inbound_high_water as u64,
        ),
        (
            "inbound_rejected_total",
            "counter",
            snapshot.inbound_rejected_total,
        ),
        (
            "outbound_rejected_total",
            "counter",
            snapshot.outbound_rejected_total,
        ),
        (
            "frame_rejected_total",
            "counter",
            snapshot.frame_rejected_total,
        ),
        (
            "inbound_timeout_total",
            "counter",
            snapshot.inbound_timeout_total,
        ),
    ] {
        writeln!(output, "# HELP roboguide_replica_transport_{name} Actual process-local transport observation.\n# TYPE roboguide_replica_transport_{name} {kind}\nroboguide_replica_transport_{name} {value}").expect("String formatting cannot fail");
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Older status absence does not become successful zero counters or invent a local limit.
    #[test]
    fn transport_metrics_keep_legacy_absence_unavailable() {
        let absent = transport_metrics(None);
        assert!(absent.contains("roboguide_replica_transport_observation_present 0\n"));
        assert!(!absent.contains("inbound_active"));
    }

    /// Actual private HTTP observations refuse missing, stale and future status without leaking IDs.
    #[tokio::test]
    async fn local_metrics_never_refreshes_stale_status_or_creates_authority() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("status.json");
        for (observed, expected) in [
            (unix_ms().unwrap(), "200 OK"),
            (0, "503 Service Unavailable"),
            (u64::MAX, "503 Service Unavailable"),
        ] {
            std::fs::write(&path, serde_json::json!({"profile":"unit-not-authority", "epoch":null,"child_pid":null,"revision":7,"observed_unix_ms":observed}).to_string()).unwrap();
            let original = std::fs::read(&path).unwrap();
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            let server = async {
                let (mut stream, _) = listener.accept().await.unwrap();
                serve(&mut stream, path.clone()).await.unwrap();
            };
            let client = async {
                let mut stream = tokio::net::TcpStream::connect(address).await.unwrap();
                stream
                    .write_all(b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n")
                    .await
                    .unwrap();
                let mut output = String::new();
                stream.read_to_string(&mut output).await.unwrap();
                output
            };
            let response = tokio::join!(server, client).1;
            assert!(
                response.starts_with(&format!("HTTP/1.1 {expected}")),
                "{response}"
            );
            assert!(!response.contains("unit-not-authority"));
            assert_eq!(std::fs::read(&path).unwrap(), original);
        }
    }
}
