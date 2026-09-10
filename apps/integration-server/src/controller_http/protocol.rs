//! Bounded Controller HTTP request framing and response encoding.

use crate::*;
/// Reads one HTTP/1.1 request using explicit header and Content-Length boundaries.
pub(crate) async fn read_control_http_request(
    stream: &mut tokio::net::TcpStream,
) -> Result<ControlHttpRequest, String> {
    use tokio::io::AsyncReadExt;
    let mut bytes = Vec::new();
    let mut chunk = [0_u8; 4096];
    let header_end = loop {
        let count = stream
            .read(&mut chunk)
            .await
            .map_err(|error| format!("read control HTTP request: {error}"))?;
        if count == 0 {
            return Err("control HTTP request ended before headers".to_string());
        }
        bytes.extend_from_slice(&chunk[..count]);
        if let Some(index) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
            let end = index + 4;
            if end > MAX_CONTROL_HTTP_HEADER_BYTES {
                return Err("control HTTP headers exceed limit".to_string());
            }
            break end;
        }
        if bytes.len() > MAX_CONTROL_HTTP_HEADER_BYTES {
            return Err("control HTTP headers exceed limit".to_string());
        }
    };
    let header_text = std::str::from_utf8(&bytes[..header_end])
        .map_err(|_| "control HTTP headers are not UTF-8".to_string())?;
    let mut lines = header_text.split("\r\n");
    let request_line = lines
        .next()
        .ok_or_else(|| "control HTTP request line is missing".to_string())?;
    let mut fields = request_line.split_whitespace();
    let method = fields
        .next()
        .ok_or_else(|| "control HTTP method is missing".to_string())?
        .to_ascii_uppercase();
    let target = fields
        .next()
        .ok_or_else(|| "control HTTP target is missing".to_string())?
        .to_string();
    let version = fields
        .next()
        .ok_or_else(|| "control HTTP version is missing".to_string())?;
    if fields.next().is_some() || !matches!(version, "HTTP/1.0" | "HTTP/1.1") {
        return Err("control HTTP request line is invalid".to_string());
    }
    let mut content_length = None;
    for line in lines.filter(|line| !line.is_empty()) {
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| "control HTTP header is malformed".to_string())?;
        if name.eq_ignore_ascii_case("transfer-encoding") {
            return Err("Transfer-Encoding is unsupported".to_string());
        }
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                return Err("duplicate Content-Length header".to_string());
            }
            content_length = Some(
                value
                    .trim()
                    .parse::<usize>()
                    .map_err(|_| "Content-Length must be an integer".to_string())?,
            );
        }
    }
    let content_length = match content_length {
        Some(length) => length,
        None if matches!(method.as_str(), "GET" | "HEAD" | "DELETE") => 0,
        None => return Err("Content-Length is required".to_string()),
    };
    if content_length > MAX_CONTROL_HTTP_BODY_BYTES {
        return Err("control HTTP body exceeds limit".to_string());
    }
    let mut body = bytes[header_end..].to_vec();
    if body.len() > content_length {
        return Err("control HTTP request contains bytes beyond Content-Length".to_string());
    }
    while body.len() < content_length {
        let remaining = content_length - body.len();
        let take = remaining.min(chunk.len());
        let count = stream
            .read(&mut chunk[..take])
            .await
            .map_err(|error| format!("read control HTTP body: {error}"))?;
        if count == 0 {
            return Err("control HTTP body ended before Content-Length".to_string());
        }
        body.extend_from_slice(&chunk[..count]);
    }
    Ok(ControlHttpRequest {
        method,
        target,
        body,
    })
}

/// Writes one bounded JSON response and closes the HTTP/1.1 connection.
pub(crate) async fn write_http_response(
    stream: &mut tokio::net::TcpStream,
    status: &str,
    body: serde_json::Value,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    use tokio::io::AsyncWriteExt;
    let body = serde_json::to_string(&body)?;
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    stream.write_all(response.as_bytes()).await?;
    stream.shutdown().await?;
    Ok(())
}

/// Parses simple URL query pairs used by the bounded event page API.
pub(crate) fn parse_query(query: &str) -> std::collections::BTreeMap<&str, &str> {
    query
        .split('&')
        .filter_map(|pair| pair.split_once('='))
        .collect()
}
