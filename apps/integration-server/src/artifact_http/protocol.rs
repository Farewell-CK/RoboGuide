//! Bounded HTTP request parsing, response encoding, and transport errors.

use super::*;
/// Initial upload-control request body.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct UploadStart {
    /// Path-safe temporary upload identity.
    pub(super) upload_id: String,
}

/// Upload finalization request body.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct UploadFinalize {
    /// Expected canonical SHA-256 digest.
    pub(super) content_digest: domain::ContentDigest,
    /// Expected exact byte count.
    pub(super) byte_size: u64,
}

/// Node replica evidence request body.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ReplicaInput {
    /// Exact manifest represented by the replica.
    pub(super) manifest: MapArtifactManifest,
    /// Node reporting the local evidence.
    pub(super) node_id: String,
    /// Mission requesting the replica operation.
    pub(super) mission_id: String,
    /// Replica lifecycle transition name.
    pub(super) status: String,
    /// Anchor used by a verification report.
    pub(super) anchor_id: Option<String>,
    /// Rejection diagnostic.
    pub(super) reason: Option<String>,
}

/// Generic Memory replica evidence request body.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct MemoryReplicaInput {
    /// Exact immutable Memory metadata represented by the replica.
    pub(super) manifest: MemoryArtifactManifest,
    /// Node reporting local staging/import evidence.
    pub(super) node_id: String,
    /// Exact receiving provider admitted by the replica Node registration.
    pub(super) consumer_provider_id: String,
    /// Monotonic generic replica transition.
    pub(super) status: String,
    /// Optional rejection diagnostic.
    pub(super) reason: Option<String>,
}

/// Parsed HTTP request headers and bytes read beyond the header delimiter.
#[derive(Debug)]
pub(super) struct RequestHead {
    /// Uppercase HTTP method.
    pub(super) method: String,
    /// Raw path without query parameters.
    pub(super) path: String,
    /// Declared request body length.
    pub(super) content_length: u64,
    /// Optional Node/session identity used by Node-authored typed evidence and Memory mutations.
    pub(super) memory_publisher: Option<MemoryPublicationIdentity>,
    /// Body prefix already read while locating the header delimiter.
    pub(super) prefetched_body: Vec<u8>,
}

/// A request head plus a fully collected bounded JSON body.
pub(super) struct Request {
    /// Raw path without query parameters.
    pub(super) path: String,
    /// JSON request body bytes.
    pub(super) body: Vec<u8>,
    /// Optional Node/session identity used by Node-authored typed evidence and Memory mutations.
    pub(super) memory_publisher: Option<MemoryPublicationIdentity>,
}

impl RequestHead {
    /// Reads a bounded JSON body after the header parser has retained its prefix.
    pub(super) async fn read_json(&mut self, stream: &mut TcpStream) -> Result<Request, HttpError> {
        if self.content_length > MAX_JSON_BODY_BYTES {
            return Err(HttpError::too_large("JSON request body exceeds limit"));
        }
        let expected = usize::try_from(self.content_length)
            .map_err(|_| HttpError::too_large("request body is too large"))?;
        Self::read_remaining(stream, &mut self.prefetched_body, expected).await?;
        Ok(Request {
            path: self.path.clone(),
            body: self.prefetched_body.clone(),
            memory_publisher: self.memory_publisher.clone(),
        })
    }

    /// Streams the declared body into a CAS upload in bounded chunks.
    pub(super) async fn stream_body(
        &self,
        stream: &mut TcpStream,
        upload: &mut ArtifactUpload,
    ) -> Result<(), HttpError> {
        let expected = usize::try_from(self.content_length)
            .map_err(|_| HttpError::too_large("request body is too large"))?;
        if self.prefetched_body.len() > expected {
            return Err(HttpError::bad_request(
                "request contains bytes beyond declared Content-Length",
            ));
        }
        let prefetched = self.prefetched_body.len();
        if prefetched > 0 {
            upload
                .write_chunk(&self.prefetched_body[..prefetched])
                .map_err(map_cas_error)?;
        }
        let mut received = prefetched;
        let mut buffer = [0_u8; 64 * 1024];
        while received < expected {
            let remaining = expected - received;
            let read_limit = remaining.min(buffer.len());
            let count = stream
                .read(&mut buffer[..read_limit])
                .await
                .map_err(|error| HttpError::internal(error.to_string()))?;
            if count == 0 {
                return Err(HttpError::bad_request("request body ended early"));
            }
            upload
                .write_chunk(&buffer[..count])
                .map_err(map_cas_error)?;
            received += count;
        }
        Ok(())
    }

    /// Reads exactly the remaining body bytes into a bounded control-request buffer.
    pub(super) async fn read_remaining(
        stream: &mut TcpStream,
        body: &mut Vec<u8>,
        expected: usize,
    ) -> Result<(), HttpError> {
        if body.len() > expected {
            return Err(HttpError::bad_request(
                "request contains bytes beyond declared Content-Length",
            ));
        }
        let mut chunk = [0_u8; 8192];
        while body.len() < expected {
            let remaining = expected - body.len();
            let read_limit = remaining.min(chunk.len());
            let count = stream
                .read(&mut chunk[..read_limit])
                .await
                .map_err(|error| HttpError::internal(error.to_string()))?;
            if count == 0 {
                return Err(HttpError::bad_request("request body ended early"));
            }
            body.extend_from_slice(&chunk[..count]);
        }
        Ok(())
    }
}

/// Reads and validates one HTTP request head while retaining only a small body prefix.
pub(super) async fn read_request_head(stream: &mut TcpStream) -> Result<RequestHead, HttpError> {
    let mut bytes = Vec::new();
    let mut chunk = [0_u8; 8192];
    loop {
        let count = stream
            .read(&mut chunk)
            .await
            .map_err(|error| HttpError::internal(error.to_string()))?;
        if count == 0 {
            return Err(HttpError::bad_request("request ended before headers"));
        }
        bytes.extend_from_slice(&chunk[..count]);
        if let Some(index) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
            return parse_request_head(bytes, index + 4);
        }
        if bytes.len() >= MAX_HEADER_BYTES {
            return Err(HttpError::headers_too_large("HTTP headers exceed limit"));
        }
    }
}

/// Parses one complete bounded HTTP/1.1 head and validates its fixed-length body framing.
pub(super) fn parse_request_head(
    bytes: Vec<u8>,
    header_end: usize,
) -> Result<RequestHead, HttpError> {
    if header_end > MAX_HEADER_BYTES {
        return Err(HttpError::headers_too_large("HTTP headers exceed limit"));
    }
    let header_text = std::str::from_utf8(&bytes[..header_end])
        .map_err(|_| HttpError::bad_request("HTTP headers are not UTF-8"))?;
    let mut lines = header_text.split("\r\n");
    let request_line = lines
        .next()
        .ok_or_else(|| HttpError::bad_request("missing request line"))?;
    let mut fields = request_line.split_whitespace();
    let method = fields
        .next()
        .ok_or_else(|| HttpError::bad_request("missing HTTP method"))?
        .to_ascii_uppercase();
    let target = fields
        .next()
        .ok_or_else(|| HttpError::bad_request("missing HTTP target"))?;
    let version = fields
        .next()
        .ok_or_else(|| HttpError::bad_request("missing HTTP version"))?;
    if fields.next().is_some() || version != "HTTP/1.1" {
        return Err(HttpError::bad_request("request line must use HTTP/1.1"));
    }
    if !target.starts_with('/') {
        return Err(HttpError::bad_request("HTTP target must be origin-form"));
    }
    let path = target.split('?').next().unwrap_or(target).to_string();
    let mut content_length = None;
    let mut has_transfer_encoding = false;
    let mut memory_node_id = None;
    let mut memory_session_id = None;
    for line in lines.filter(|line| !line.is_empty()) {
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| HttpError::bad_request("malformed HTTP header"))?;
        if name.is_empty() || name.trim() != name {
            return Err(HttpError::bad_request("malformed HTTP header name"));
        }
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                return Err(HttpError::bad_request("duplicate Content-Length header"));
            }
            let value = value.trim();
            if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
                return Err(HttpError::bad_request(
                    "Content-Length must contain decimal digits only",
                ));
            }
            let parsed = value
                .parse::<u64>()
                .map_err(|_| HttpError::bad_request("Content-Length must be an integer"))?;
            content_length = Some(parsed);
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            has_transfer_encoding = true;
        } else if name.eq_ignore_ascii_case("x-roboguide-node-id") {
            if memory_node_id.replace(value.trim().to_string()).is_some() {
                return Err(HttpError::bad_request(
                    "duplicate X-RoboGuide-Node-Id header",
                ));
            }
        } else if name.eq_ignore_ascii_case("x-roboguide-session-id")
            && memory_session_id
                .replace(value.trim().to_string())
                .is_some()
        {
            return Err(HttpError::bad_request(
                "duplicate X-RoboGuide-Session-Id header",
            ));
        }
    }
    if has_transfer_encoding {
        return Err(HttpError::bad_request(
            "Transfer-Encoding is unsupported; use Content-Length",
        ));
    }
    let content_length = match content_length {
        Some(length) => length,
        // Bodyless reads are valid without an explicit header.  Mutating endpoints still
        // require Content-Length so the streaming parser never has to guess where bytes end.
        None if matches!(method.as_str(), "GET" | "HEAD" | "DELETE") => 0,
        None => return Err(HttpError::bad_request("Content-Length is required")),
    };
    if content_length > MAX_ARTIFACT_BYTES {
        return Err(HttpError::too_large("request body exceeds artifact limit"));
    }
    let memory_publisher = match (memory_node_id, memory_session_id) {
        (Some(node_id), Some(session_id)) if !session_id.is_empty() => {
            Some(MemoryPublicationIdentity {
                node_id: NodeId::new(node_id)
                    .map_err(|error| HttpError::bad_request(error.to_string()))?,
                session_id,
            })
        }
        (None, None) => None,
        _ => {
            return Err(HttpError::bad_request(
                "generic Memory publisher requires both Node and session headers",
            ));
        }
    };
    let prefetched_body = bytes[header_end..].to_vec();
    let expected = usize::try_from(content_length)
        .map_err(|_| HttpError::too_large("request body is too large"))?;
    if prefetched_body.len() > expected {
        return Err(HttpError::bad_request(
            "request contains bytes beyond declared Content-Length",
        ));
    }
    Ok(RequestHead {
        method,
        path,
        content_length,
        memory_publisher,
        prefetched_body,
    })
}

/// Decodes a bounded JSON request body into a typed control structure.
pub(super) fn parse_json<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T, HttpError> {
    serde_json::from_slice(body)
        .map_err(|error| HttpError::bad_request(format!("invalid JSON body: {error}")))
}

/// Returns the local receive timestamp used for artifact evidence ordering.
pub(super) fn receive_timestamp() -> TimestampMs {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(u128::from(u64::MAX)) as u64)
        .unwrap_or_default();
    TimestampMs::new(millis)
}

/// One successful response variant used by the small HTTP transport.
pub(super) enum Response {
    /// JSON response body.
    Json(&'static str, serde_json::Value),
}

impl Response {
    /// Writes a JSON response and closes the connection.
    pub(super) async fn write(self, stream: &mut TcpStream) -> Result<(), HttpError> {
        match self {
            Self::Json(status, body) => write_json(stream, status, &body).await,
        }
    }
}

/// Writes one JSON response with a fixed content length.
pub(super) async fn write_json(
    stream: &mut TcpStream,
    status: &str,
    body: &serde_json::Value,
) -> Result<(), HttpError> {
    let body = serde_json::to_vec(body).map_err(|error| HttpError::internal(error.to_string()))?;
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream
        .write_all(response.as_bytes())
        .await
        .map_err(|error| HttpError::internal(error.to_string()))?;
    stream
        .write_all(&body)
        .await
        .map_err(|error| HttpError::internal(error.to_string()))?;
    stream
        .shutdown()
        .await
        .map_err(|error| HttpError::internal(error.to_string()))?;
    Ok(())
}

/// HTTP error carrying a status and JSON-safe diagnostic.
#[derive(Debug)]
pub(super) struct HttpError {
    /// HTTP status line.
    pub(super) status: &'static str,
    /// Stable diagnostic message.
    pub(super) message: String,
}

impl HttpError {
    /// Builds a 400 error.
    pub(super) fn bad_request(message: impl Into<String>) -> Self {
        Self {
            status: "400 Bad Request",
            message: message.into(),
        }
    }

    /// Builds a 404 error.
    pub(super) fn not_found(message: impl Into<String>) -> Self {
        Self {
            status: "404 Not Found",
            message: message.into(),
        }
    }

    /// Builds a 408 error for a body stream that exceeded its bounded receive window.
    pub(super) fn request_timeout(message: impl Into<String>) -> Self {
        Self {
            status: "408 Request Timeout",
            message: message.into(),
        }
    }

    /// Builds a 409 error.
    pub(super) fn conflict(message: impl Into<String>) -> Self {
        Self {
            status: "409 Conflict",
            message: message.into(),
        }
    }

    /// Builds a 403 error for claims outside current provider ownership.
    pub(super) fn forbidden(message: impl Into<String>) -> Self {
        Self {
            status: "403 Forbidden",
            message: message.into(),
        }
    }

    /// Builds a 413 error.
    pub(super) fn too_large(message: impl Into<String>) -> Self {
        Self {
            status: "413 Payload Too Large",
            message: message.into(),
        }
    }

    /// Builds a 431 error for a request head that exceeds the fixed parser bound.
    pub(super) fn headers_too_large(message: impl Into<String>) -> Self {
        Self {
            status: "431 Request Header Fields Too Large",
            message: message.into(),
        }
    }

    /// Builds a 429 error when the process-local active upload count is exhausted.
    pub(super) fn resource_exhausted(message: impl Into<String>) -> Self {
        Self {
            status: "429 Too Many Requests",
            message: message.into(),
        }
    }

    /// Builds a 500 error.
    pub(super) fn internal(message: impl Into<String>) -> Self {
        Self {
            status: "500 Internal Server Error",
            message: message.into(),
        }
    }

    /// Builds a 503 error when durable state requires process recovery.
    pub(super) fn service_unavailable(message: impl Into<String>) -> Self {
        Self {
            status: "503 Service Unavailable",
            message: message.into(),
        }
    }

    /// Returns the status line.
    pub(super) fn status(&self) -> &'static str {
        self.status
    }

    /// Converts this error into a JSON body.
    pub(super) fn body(&self) -> serde_json::Value {
        serde_json::json!({"error": self.message})
    }
}

/// Maps Artifact Store failures into stable HTTP statuses without leaking local paths.
pub(super) fn map_cas_error(error: CasError) -> HttpError {
    match error {
        CasError::InvalidDigest { .. }
        | CasError::InvalidUploadId { .. }
        | CasError::UploadClosed { .. } => HttpError::bad_request(error.to_string()),
        CasError::ArtifactNotFound { .. } | CasError::UploadNotFound { .. } => {
            HttpError::not_found(error.to_string())
        }
        CasError::DigestMismatch { .. }
        | CasError::SizeMismatch { .. }
        | CasError::ArtifactConflict { .. }
        | CasError::UploadAlreadyExists { .. } => HttpError::conflict(error.to_string()),
        CasError::Io { .. } => HttpError::internal(error.to_string()),
    }
}
