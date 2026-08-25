//! Generic fixed-route JSON-over-HTTP local driver.

use crate::local_engine::driver::{
    BoxDriverFuture, CompiledDriverRequest, DriverError, DriverEvent, DriverKind, DriverResponse,
    LocalDriver,
};
use crate::local_engine::http_transport::{
    build_client, request_target, require_success, resolve_headers,
};
use std::time::Duration;
use tokio::sync::mpsc;

/// Executes configuration-compiled JSON HTTP requests exactly once.
#[derive(Clone)]
pub struct HttpDriver {
    /// Shared client with redirects and automatic retries disabled.
    client: reqwest::Client,
}

impl HttpDriver {
    /// Creates a constrained HTTP driver without opening a connection.
    pub fn new() -> Result<Self, DriverError> {
        Ok(Self {
            client: build_client(None)?,
        })
    }
}

impl LocalDriver for HttpDriver {
    /// Identifies this implementation as the JSON HTTP driver.
    fn kind(&self) -> DriverKind {
        DriverKind::Http
    }

    /// Sends one fixed-method request and returns its JSON response as one terminal event.
    fn invoke<'a>(&'a self, request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
        Box::pin(async move {
            let CompiledDriverRequest::Http {
                endpoint,
                method,
                path,
                credential_headers,
                body,
                timeout_ms,
            } = request
            else {
                return Err(DriverError::KindMismatch);
            };
            let method = reqwest::Method::from_bytes(method.as_bytes()).map_err(|error| {
                DriverError::Transport(format!("invalid configured HTTP method: {error}"))
            })?;
            let (client, target) = request_target(&self.client, endpoint, Some(path))?;
            let response = client
                .request(method, target)
                .headers(resolve_headers(credential_headers)?)
                .timeout(Duration::from_millis(*timeout_ms))
                .json(body)
                .send()
                .await
                .map_err(|error| DriverError::Transport(error.to_string()))?;
            require_success(&response)?;
            let payload = response
                .json::<serde_json::Value>()
                .await
                .map_err(|error| DriverError::InvalidResponse(error.to_string()))?;
            Ok(single_event_response(payload))
        })
    }
}

/// Wraps one unary response value in the common driver event stream.
fn single_event_response(payload: serde_json::Value) -> DriverResponse {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// Serves one deterministic HTTP response and returns the captured request bytes.
    async fn serve_once(response: &'static str) -> (String, tokio::task::JoinHandle<Vec<u8>>) {
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
                if count == 0 {
                    break;
                }
                request.extend_from_slice(&buffer[..count]);
                if complete_http_request(&request) {
                    break;
                }
            }
            stream
                .write_all(response.as_bytes())
                .await
                .expect("response writes");
            request
        });
        (format!("http://{address}"), task)
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

    /// Fixed routing and rendered JSON reach the local endpoint once.
    #[tokio::test]
    async fn invokes_fixed_json_endpoint_once() {
        let (endpoint, server) = serve_once(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 26\r\nConnection: close\r\n\r\n{\"state\":\"RUNNING\",\"id\":7}",
        )
        .await;
        let request = CompiledDriverRequest::Http {
            endpoint,
            method: "POST".to_string(),
            path: "/execute".to_string(),
            credential_headers: BTreeMap::new(),
            body: serde_json::json!({"goal": "dock"}),
            timeout_ms: 1_000,
        };
        let mut response = HttpDriver::new()
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
            serde_json::json!({"state": "RUNNING", "id": 7})
        );
        assert!(event.terminal);
        let request_bytes = server.await.expect("server exits");
        let request_text = String::from_utf8(request_bytes).expect("request is UTF-8");
        assert!(request_text.starts_with("POST /execute HTTP/1.1\r\n"));
        assert!(request_text.contains("{\"goal\":\"dock\"}"));
    }

    /// Redirect responses are surfaced instead of following a server-selected target.
    #[tokio::test]
    async fn rejects_redirect_without_second_dispatch() {
        let (endpoint, server) = serve_once(
            "HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/elsewhere\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        )
        .await;
        let request = CompiledDriverRequest::Http {
            endpoint,
            method: "GET".to_string(),
            path: "/status".to_string(),
            credential_headers: BTreeMap::new(),
            body: serde_json::json!({}),
            timeout_ms: 1_000,
        };
        let error = HttpDriver::new()
            .expect("driver builds")
            .invoke(&request)
            .await
            .err()
            .expect("redirect fails");
        assert!(matches!(error, DriverError::Transport(detail) if detail.contains("302")));
        server.await.expect("server exits");
    }
}
