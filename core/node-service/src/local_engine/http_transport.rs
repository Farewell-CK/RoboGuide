//! Shared constrained HTTP transport helpers for local HTTP-based drivers.

use crate::local_engine::driver::DriverError;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use std::collections::BTreeMap;
use std::path::Path;

/// Builds an HTTP client that neither retries nor follows server-controlled redirects.
pub(super) fn build_client(unix_socket: Option<&Path>) -> Result<reqwest::Client, DriverError> {
    let builder = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never());
    #[cfg(unix)]
    let builder = if let Some(path) = unix_socket {
        builder.unix_socket(path)
    } else {
        builder
    };
    #[cfg(not(unix))]
    let builder = if unix_socket.is_some() {
        return Err(DriverError::Transport(
            "Unix-socket HTTP is unavailable on this platform".to_string(),
        ));
    } else {
        builder
    };
    builder
        .build()
        .map_err(|error| DriverError::Transport(error.to_string()))
}

/// Resolves configured credential environment variables into validated HTTP headers.
pub(super) fn resolve_headers(
    credentials: &BTreeMap<String, String>,
) -> Result<HeaderMap, DriverError> {
    credentials
        .iter()
        .map(|(name, environment_variable)| {
            let name = HeaderName::from_bytes(name.as_bytes()).map_err(|error| {
                DriverError::InvalidResponse(format!(
                    "configured credential header name is invalid: {error}"
                ))
            })?;
            let secret = std::env::var(environment_variable)
                .map_err(|_| DriverError::MissingCredential(environment_variable.clone()))?;
            let value = HeaderValue::from_str(&secret).map_err(|error| {
                DriverError::InvalidResponse(format!(
                    "credential header value from environment variable `{environment_variable}` is invalid: {error}"
                ))
            })?;
            Ok((name, value))
        })
        .collect()
}

/// Builds a request URL and, for Unix endpoints, a socket-specific no-retry client.
pub(super) fn request_target(
    default_client: &reqwest::Client,
    endpoint: &str,
    path: Option<&str>,
) -> Result<(reqwest::Client, String), DriverError> {
    let endpoint_url = url::Url::parse(endpoint)
        .map_err(|error| DriverError::Transport(format!("invalid local endpoint: {error}")))?;
    if endpoint_url.scheme() == "unix" {
        let path = path.unwrap_or("/mcp");
        validate_fixed_path(path)?;
        let client = build_client(Some(Path::new(endpoint_url.path())))?;
        return Ok((client, format!("http://localhost{path}")));
    }
    let target = match path {
        Some(path) => {
            validate_fixed_path(path)?;
            format!("{}{path}", endpoint.trim_end_matches('/'))
        }
        None => endpoint.to_string(),
    };
    Ok((default_client.clone(), target))
}

/// Rejects request paths that could alter the configured endpoint or carry dynamic routing data.
fn validate_fixed_path(path: &str) -> Result<(), DriverError> {
    if path.starts_with('/') && !path.contains(['{', '}', '$', '?', '#']) && !path.contains("..") {
        Ok(())
    } else {
        Err(DriverError::Transport(
            "configured HTTP path is not a fixed absolute path".to_string(),
        ))
    }
}

/// Converts a non-success HTTP response into a transport failure without exposing its body.
pub(super) fn require_success(response: &reqwest::Response) -> Result<(), DriverError> {
    if response.status().is_success() {
        Ok(())
    } else {
        Err(DriverError::Transport(format!(
            "local HTTP endpoint returned status {}",
            response.status()
        )))
    }
}
