//! Generic MCP Streamable HTTP tool driver.

use crate::local_engine::driver::{
    BoxDriverFuture, CompiledDriverRequest, DriverError, DriverEvent, DriverKind, DriverResponse,
    LocalDriver,
};
use crate::local_engine::http_transport::{
    build_client, request_target, require_success, resolve_headers,
};
use reqwest::header::{ACCEPT, CONTENT_TYPE, HeaderName, HeaderValue};
use serde_json::Value;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use tokio::sync::mpsc;

/// Latest stable MCP protocol revision implemented by this transport driver.
const MCP_PROTOCOL_VERSION: &str = "2025-11-25";

/// Executes one configuration-selected MCP tool over Streamable HTTP.
pub struct McpDriver {
    /// Shared client with redirects and automatic retries disabled.
    client: reqwest::Client,
    /// Process-local JSON-RPC request ID allocator.
    next_request_id: AtomicU64,
}

impl McpDriver {
    /// Creates a constrained MCP driver without opening a connection.
    pub fn new() -> Result<Self, DriverError> {
        Ok(Self {
            client: build_client(None)?,
            next_request_id: AtomicU64::new(1),
        })
    }

    /// Allocates a non-zero request ID without coupling it to execution identity.
    fn allocate_request_id(&self) -> u64 {
        let id = self.next_request_id.fetch_add(1, Ordering::Relaxed);
        if id == 0 {
            self.next_request_id.fetch_add(1, Ordering::Relaxed)
        } else {
            id
        }
    }
}

impl LocalDriver for McpDriver {
    /// Identifies this implementation as the MCP Streamable HTTP driver.
    fn kind(&self) -> DriverKind {
        DriverKind::Mcp
    }

    /// Sends one `tools/call` JSON-RPC request and handles JSON or SSE responses.
    fn invoke<'a>(&'a self, request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
        Box::pin(async move {
            let CompiledDriverRequest::Mcp {
                endpoint,
                tool,
                credential_headers,
                arguments,
                timeout_ms,
            } = request
            else {
                return Err(DriverError::KindMismatch);
            };
            if !arguments.is_object() {
                return Err(DriverError::InvalidResponse(
                    "MCP tool arguments must be a JSON object".to_string(),
                ));
            }
            let request_id = self.allocate_request_id();
            let mut headers = resolve_headers(credential_headers)?;
            headers.insert(
                ACCEPT,
                HeaderValue::from_static("application/json, text/event-stream"),
            );
            headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
            let protocol_header = HeaderName::from_static("mcp-protocol-version");
            if !headers.contains_key(&protocol_header) {
                headers.insert(
                    protocol_header,
                    HeaderValue::from_static(MCP_PROTOCOL_VERSION),
                );
            }
            let (_, target) = request_target(&self.client, endpoint, None)?;
            let response = self
                .client
                .post(target)
                .headers(headers)
                .timeout(Duration::from_millis(*timeout_ms))
                .json(&serde_json::json!({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": tool,
                        "arguments": arguments,
                    },
                }))
                .send()
                .await
                .map_err(|error| DriverError::Transport(error.to_string()))?;
            require_success(&response)?;
            let event_stream = response
                .headers()
                .get(CONTENT_TYPE)
                .and_then(|value| value.to_str().ok())
                .is_some_and(|value| {
                    value
                        .split(';')
                        .next()
                        .is_some_and(|media_type| media_type.trim() == "text/event-stream")
                });
            if event_stream {
                Ok(streaming_response(response, Value::from(request_id)))
            } else {
                let envelope = response
                    .json::<Value>()
                    .await
                    .map_err(|error| DriverError::InvalidResponse(error.to_string()))?;
                let payload = decode_response_envelope(envelope, &Value::from(request_id))?;
                Ok(single_event_response(payload))
            }
        })
    }
}

/// Wraps one MCP JSON response in the common driver event stream.
fn single_event_response(payload: Value) -> DriverResponse {
    let (sender, receiver) = mpsc::channel(1);
    sender
        .try_send(Ok(DriverEvent {
            sequence: 0,
            payload,
            terminal: true,
        }))
        .expect("new response channel has capacity for one event");
    DriverResponse { events: receiver }
}

/// Starts background decoding for an MCP SSE response already accepted by the server.
fn streaming_response(response: reqwest::Response, expected_id: Value) -> DriverResponse {
    let (sender, receiver) = mpsc::channel(16);
    tokio::spawn(async move {
        consume_sse_response(response, expected_id, sender).await;
    });
    DriverResponse { events: receiver }
}

/// Decodes SSE messages until the matching JSON-RPC response terminates the request.
async fn consume_sse_response(
    mut response: reqwest::Response,
    expected_id: Value,
    sender: mpsc::Sender<Result<DriverEvent, DriverError>>,
) {
    let mut decoder = SseDecoder::default();
    let mut sequence = 0_u64;
    loop {
        let chunk = match response.chunk().await {
            Ok(Some(chunk)) => chunk,
            Ok(None) => {
                let messages = match decoder.finish() {
                    Ok(messages) => messages,
                    Err(error) => {
                        let _ = sender.send(Err(error)).await;
                        return;
                    }
                };
                if emit_sse_messages(messages, &expected_id, &sender, &mut sequence).await {
                    return;
                }
                let _ = sender
                    .send(Err(DriverError::InvalidResponse(
                        "MCP SSE stream ended before the matching JSON-RPC response".to_string(),
                    )))
                    .await;
                return;
            }
            Err(error) => {
                let _ = sender
                    .send(Err(DriverError::Transport(error.to_string())))
                    .await;
                return;
            }
        };
        let messages = match decoder.push(&chunk) {
            Ok(messages) => messages,
            Err(error) => {
                let _ = sender.send(Err(error)).await;
                return;
            }
        };
        if emit_sse_messages(messages, &expected_id, &sender, &mut sequence).await {
            return;
        }
    }
}

/// Emits decoded notifications and returns true after the matching terminal response or failure.
async fn emit_sse_messages(
    messages: Vec<String>,
    expected_id: &Value,
    sender: &mpsc::Sender<Result<DriverEvent, DriverError>>,
    sequence: &mut u64,
) -> bool {
    for source in messages {
        if source.trim().is_empty() {
            continue;
        }
        let envelope = match serde_json::from_str::<Value>(&source) {
            Ok(value) => value,
            Err(error) => {
                let _ = sender
                    .send(Err(DriverError::InvalidResponse(format!(
                        "invalid JSON in MCP SSE event: {error}"
                    ))))
                    .await;
                return true;
            }
        };
        match decode_stream_envelope(envelope, expected_id) {
            Ok(StreamEnvelope::Notification(payload)) => {
                let event = DriverEvent {
                    sequence: *sequence,
                    payload,
                    terminal: false,
                };
                *sequence = sequence.saturating_add(1);
                if sender.send(Ok(event)).await.is_err() {
                    return true;
                }
            }
            Ok(StreamEnvelope::Response(payload)) => {
                let event = DriverEvent {
                    sequence: *sequence,
                    payload,
                    terminal: true,
                };
                let _ = sender.send(Ok(event)).await;
                return true;
            }
            Err(error) => {
                let _ = sender.send(Err(error)).await;
                return true;
            }
        }
    }
    false
}

/// One decoded JSON-RPC message carried by an MCP SSE event.
enum StreamEnvelope {
    /// Server notification related to the pending tool call.
    Notification(Value),
    /// Matching tool-call response.
    Response(Value),
}

/// Validates one JSON-RPC response and extracts its result value.
fn decode_response_envelope(envelope: Value, expected_id: &Value) -> Result<Value, DriverError> {
    match decode_stream_envelope(envelope, expected_id)? {
        StreamEnvelope::Response(payload) => Ok(payload),
        StreamEnvelope::Notification(_) => Err(DriverError::InvalidResponse(
            "MCP JSON response was a notification instead of a tool result".to_string(),
        )),
    }
}

/// Validates one JSON-RPC envelope while preserving related server notifications.
fn decode_stream_envelope(
    envelope: Value,
    expected_id: &Value,
) -> Result<StreamEnvelope, DriverError> {
    let object = envelope.as_object().ok_or_else(|| {
        DriverError::InvalidResponse("MCP response must be a JSON object".to_string())
    })?;
    if object.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
        return Err(DriverError::InvalidResponse(
            "MCP response has no supported JSON-RPC version".to_string(),
        ));
    }
    match object.get("id") {
        Some(id) if id == expected_id => {
            if let Some(error) = object.get("error") {
                return Err(DriverError::InvalidResponse(format_json_rpc_error(error)));
            }
            object
                .get("result")
                .cloned()
                .map(StreamEnvelope::Response)
                .ok_or_else(|| {
                    DriverError::InvalidResponse(
                        "MCP response contains neither result nor error".to_string(),
                    )
                })
        }
        Some(_) => Err(DriverError::InvalidResponse(
            "MCP response ID does not match the request".to_string(),
        )),
        None if object.get("method").and_then(Value::as_str).is_some() => {
            Ok(StreamEnvelope::Notification(envelope))
        }
        None => Err(DriverError::InvalidResponse(
            "MCP message contains neither an ID nor a notification method".to_string(),
        )),
    }
}

/// Formats a JSON-RPC protocol error without assuming optional data shape.
fn format_json_rpc_error(error: &Value) -> String {
    let code = error
        .get("code")
        .map(Value::to_string)
        .unwrap_or_else(|| "unknown".to_string());
    let message = error
        .get("message")
        .and_then(Value::as_str)
        .unwrap_or("unspecified error");
    format!("MCP JSON-RPC error {code}: {message}")
}

/// Incremental UTF-8 SSE data-field decoder.
#[derive(Default)]
struct SseDecoder {
    /// Bytes not yet terminated by a line-feed.
    pending: Vec<u8>,
    /// Data fields accumulated for the current event.
    data_lines: Vec<String>,
}

impl SseDecoder {
    /// Consumes an arbitrary response chunk and returns every completed event payload.
    fn push(&mut self, chunk: &[u8]) -> Result<Vec<String>, DriverError> {
        self.pending.extend_from_slice(chunk);
        let mut messages = Vec::new();
        while let Some(line_end) = self.pending.iter().position(|byte| *byte == b'\n') {
            let mut line = self.pending.drain(..=line_end).collect::<Vec<_>>();
            line.pop();
            if line.last() == Some(&b'\r') {
                line.pop();
            }
            self.process_line(&line, &mut messages)?;
        }
        Ok(messages)
    }

    /// Flushes a final unterminated line and event when the HTTP body closes.
    fn finish(&mut self) -> Result<Vec<String>, DriverError> {
        let mut messages = Vec::new();
        if !self.pending.is_empty() {
            let line = std::mem::take(&mut self.pending);
            self.process_line(&line, &mut messages)?;
        }
        if !self.data_lines.is_empty() {
            messages.push(self.data_lines.join("\n"));
            self.data_lines.clear();
        }
        Ok(messages)
    }

    /// Applies one SSE field line to the current event accumulator.
    fn process_line(&mut self, line: &[u8], messages: &mut Vec<String>) -> Result<(), DriverError> {
        let line = std::str::from_utf8(line).map_err(|error| {
            DriverError::InvalidResponse(format!("MCP SSE response is not UTF-8: {error}"))
        })?;
        if line.is_empty() {
            if !self.data_lines.is_empty() {
                messages.push(self.data_lines.join("\n"));
                self.data_lines.clear();
            }
            return Ok(());
        }
        if line.starts_with(':') {
            return Ok(());
        }
        let (field, value) = line.split_once(':').unwrap_or((line, ""));
        let value = value.strip_prefix(' ').unwrap_or(value);
        if field == "data" {
            self.data_lines.push(value.to_string());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// Serves one deterministic MCP response and returns captured request bytes.
    async fn serve_once(response: String) -> (String, tokio::task::JoinHandle<Vec<u8>>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test listener binds");
        let address = listener.local_addr().expect("test address exists");
        let task = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.expect("request connects");
            let mut request = Vec::new();
            let mut buffer = [0_u8; 1024];
            loop {
                let count = stream.read(&mut buffer).await.expect("request reads");
                request.extend_from_slice(&buffer[..count]);
                if count == 0 || complete_http_request(&request) {
                    break;
                }
            }
            stream
                .write_all(response.as_bytes())
                .await
                .expect("response writes");
            request
        });
        (format!("http://{address}/mcp"), task)
    }

    /// Reports whether headers and the declared request body have been received.
    fn complete_http_request(request: &[u8]) -> bool {
        let Some(header_end) = request.windows(4).position(|window| window == b"\r\n\r\n") else {
            return false;
        };
        let headers = String::from_utf8_lossy(&request[..header_end]);
        let content_length = headers
            .lines()
            .find_map(|line| {
                line.to_ascii_lowercase()
                    .strip_prefix("content-length: ")
                    .map(str::to_string)
            })
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(0);
        request.len() >= header_end + 4 + content_length
    }

    /// A direct JSON tool result is unwrapped into one terminal driver event.
    #[tokio::test]
    async fn invokes_tool_with_streamable_http_headers() {
        let body = r#"{"jsonrpc":"2.0","id":1,"result":{"state":"RUNNING","handle":"r-7"}}"#;
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let (endpoint, server) = serve_once(response).await;
        let request = CompiledDriverRequest::Mcp {
            endpoint,
            tool: "navigate".to_string(),
            credential_headers: BTreeMap::new(),
            arguments: serde_json::json!({"region": "dock"}),
            timeout_ms: 1_000,
        };
        let mut response = McpDriver::new()
            .expect("driver builds")
            .invoke(&request)
            .await
            .expect("request succeeds")
            .events;
        let event = response
            .recv()
            .await
            .expect("event exists")
            .expect("event succeeds");
        assert_eq!(
            event.payload,
            serde_json::json!({"state": "RUNNING", "handle": "r-7"})
        );
        assert!(event.terminal);
        let request =
            String::from_utf8(server.await.expect("server exits")).expect("request is UTF-8");
        assert!(request.starts_with("POST /mcp HTTP/1.1\r\n"));
        assert!(
            request
                .to_ascii_lowercase()
                .contains("accept: application/json, text/event-stream\r\n")
        );
        assert!(request.contains("\"method\":\"tools/call\""));
        assert!(request.contains("\"name\":\"navigate\""));
    }

    /// SSE notifications remain ordered before the matching terminal tool result.
    #[tokio::test]
    async fn streams_notifications_before_tool_result() {
        let sse = concat!(
            "data: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\",\"params\":{\"progress\":1}}\n\n",
            "data: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"state\":\"DONE\"}}\n\n"
        );
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{sse}",
            sse.len()
        );
        let (endpoint, server) = serve_once(response).await;
        let request = CompiledDriverRequest::Mcp {
            endpoint,
            tool: "navigate".to_string(),
            credential_headers: BTreeMap::new(),
            arguments: serde_json::json!({}),
            timeout_ms: 1_000,
        };
        let mut events = McpDriver::new()
            .expect("driver builds")
            .invoke(&request)
            .await
            .expect("request succeeds")
            .events;
        let notification = events
            .recv()
            .await
            .expect("notification exists")
            .expect("valid event");
        let result = events
            .recv()
            .await
            .expect("result exists")
            .expect("valid result");
        assert_eq!(notification.sequence, 0);
        assert!(!notification.terminal);
        assert_eq!(result.sequence, 1);
        assert_eq!(result.payload, serde_json::json!({"state": "DONE"}));
        assert!(result.terminal);
        server.await.expect("server exits");
    }
}
