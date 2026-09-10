//! Dynamic gRPC driver tests.

use super::*;
use prost_types::{
    DescriptorProto, FieldDescriptorProto, FileDescriptorProto, FileDescriptorSet,
    MethodDescriptorProto, ServiceDescriptorProto,
    field_descriptor_proto::{Label, Type},
};
use std::convert::Infallible;
use std::task::{Context, Poll};
use tonic::codegen::{Body, BoxFuture, Service, StdError};

/// Generated-shape request used only by the deterministic local gRPC test server.
#[derive(Clone, PartialEq, prost::Message)]
struct EchoRequest {
    /// Text copied into local response facts.
    #[prost(string, tag = "1")]
    text: String,
}

/// Generated-shape response used only by the deterministic local gRPC test server.
#[derive(Clone, PartialEq, prost::Message)]
struct EchoResponse {
    /// Text returned to the dynamic client.
    #[prost(string, tag = "1")]
    text: String,
}

/// Unary echo RPC implementation for the local test server.
#[derive(Clone, Copy)]
struct UnaryEcho;

impl Service<Request<EchoRequest>> for UnaryEcho {
    type Response = tonic::Response<EchoResponse>;
    type Error = Status;
    type Future = BoxFuture<Self::Response, Self::Error>;

    /// The deterministic test service is always ready.
    fn poll_ready(&mut self, _context: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(()))
    }

    /// Copies one unary request into one response.
    fn call(&mut self, request: Request<EchoRequest>) -> Self::Future {
        Box::pin(async move {
            Ok(tonic::Response::new(EchoResponse {
                text: request.into_inner().text,
            }))
        })
    }
}

/// Server-streaming echo RPC implementation for the local test server.
#[derive(Clone, Copy)]
struct WatchEcho;

impl Service<Request<EchoRequest>> for WatchEcho {
    type Response =
        tonic::Response<tokio_stream::Iter<std::vec::IntoIter<Result<EchoResponse, Status>>>>;
    type Error = Status;
    type Future = BoxFuture<Self::Response, Self::Error>;

    /// The deterministic test service is always ready.
    fn poll_ready(&mut self, _context: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(()))
    }

    /// Returns two ordered responses derived from one request.
    fn call(&mut self, request: Request<EchoRequest>) -> Self::Future {
        let text = request.into_inner().text;
        Box::pin(async move {
            Ok(tonic::Response::new(tokio_stream::iter(vec![
                Ok(EchoResponse {
                    text: format!("{text}-1"),
                }),
                Ok(EchoResponse {
                    text: format!("{text}-2"),
                }),
            ])))
        })
    }
}

/// Minimal tonic router exposing methods described by [`descriptor_set`].
#[derive(Clone, Copy)]
struct EchoService;

impl<B> Service<http::Request<B>> for EchoService
where
    B: Body + Send + 'static,
    B::Error: Into<StdError> + Send + 'static,
{
    type Response = http::Response<tonic::body::Body>;
    type Error = Infallible;
    type Future = BoxFuture<Self::Response, Self::Error>;

    /// The deterministic test router is always ready.
    fn poll_ready(&mut self, _context: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(()))
    }

    /// Dispatches only the two fixed descriptor-backed test methods.
    fn call(&mut self, request: http::Request<B>) -> Self::Future {
        match request.uri().path() {
            "/local.test.Echo/Unary" => Box::pin(async move {
                let codec = tonic_prost::ProstCodec::default();
                let mut grpc = tonic::server::Grpc::new(codec);
                Ok(grpc.unary(UnaryEcho, request).await)
            }),
            "/local.test.Echo/Watch" => Box::pin(async move {
                let codec = tonic_prost::ProstCodec::default();
                let mut grpc = tonic::server::Grpc::new(codec);
                Ok(grpc.server_streaming(WatchEcho, request).await)
            }),
            _ => Box::pin(async move {
                let mut response = http::Response::new(tonic::body::Body::default());
                response.headers_mut().insert(
                    tonic::Status::GRPC_STATUS,
                    (tonic::Code::Unimplemented as i32).into(),
                );
                response.headers_mut().insert(
                    http::header::CONTENT_TYPE,
                    tonic::metadata::GRPC_CONTENT_TYPE,
                );
                Ok(response)
            }),
        }
    }
}

impl tonic::server::NamedService for EchoService {
    const NAME: &'static str = "local.test.Echo";
}

/// Builds a minimal descriptor set containing unary and server-streaming echo methods.
fn descriptor_set() -> Vec<u8> {
    let message = |name: &str| DescriptorProto {
        name: Some(name.to_string()),
        field: vec![FieldDescriptorProto {
            name: Some("text".to_string()),
            number: Some(1),
            label: Some(Label::Optional as i32),
            r#type: Some(Type::String as i32),
            json_name: Some("text".to_string()),
            ..FieldDescriptorProto::default()
        }],
        ..DescriptorProto::default()
    };
    FileDescriptorSet {
        file: vec![FileDescriptorProto {
            name: Some("echo.proto".to_string()),
            package: Some("local.test".to_string()),
            syntax: Some("proto3".to_string()),
            message_type: vec![message("EchoRequest"), message("EchoResponse")],
            service: vec![ServiceDescriptorProto {
                name: Some("Echo".to_string()),
                method: vec![
                    MethodDescriptorProto {
                        name: Some("Unary".to_string()),
                        input_type: Some(".local.test.EchoRequest".to_string()),
                        output_type: Some(".local.test.EchoResponse".to_string()),
                        ..MethodDescriptorProto::default()
                    },
                    MethodDescriptorProto {
                        name: Some("Watch".to_string()),
                        input_type: Some(".local.test.EchoRequest".to_string()),
                        output_type: Some(".local.test.EchoResponse".to_string()),
                        server_streaming: Some(true),
                        ..MethodDescriptorProto::default()
                    },
                ],
                ..ServiceDescriptorProto::default()
            }],
            ..FileDescriptorProto::default()
        }],
    }
    .encode_to_vec()
}

/// Returns process-lifetime descriptor bytes suitable for the reflection server.
fn static_descriptor_set() -> &'static [u8] {
    static DESCRIPTOR: std::sync::OnceLock<Vec<u8>> = std::sync::OnceLock::new();
    DESCRIPTOR.get_or_init(descriptor_set).as_slice()
}

/// Starts the deterministic local gRPC server without using network services outside the test.
async fn start_echo_server() -> (
    String,
    tokio::task::JoinHandle<Result<(), tonic::transport::Error>>,
) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("test listener binds");
    let address = listener.local_addr().expect("test address exists");
    let incoming = tokio_stream::wrappers::TcpListenerStream::new(listener);
    let reflection = tonic_reflection::server::Builder::configure()
        .register_encoded_file_descriptor_set(static_descriptor_set())
        .build_v1()
        .expect("reflection service builds");
    let server = tokio::spawn(async move {
        tonic::transport::Server::builder()
            .add_service(EchoService)
            .add_service(reflection)
            .serve_with_incoming(incoming)
            .await
    });
    (format!("http://{address}"), server)
}

/// Starts the same deterministic service over a Unix Domain Socket.
#[cfg(unix)]
async fn start_echo_uds(
    socket_path: &std::path::Path,
) -> tokio::task::JoinHandle<Result<(), tonic::transport::Error>> {
    let listener = tokio::net::UnixListener::bind(socket_path).expect("Unix listener binds");
    let incoming = tokio_stream::wrappers::UnixListenerStream::new(listener);
    tokio::spawn(async move {
        tonic::transport::Server::builder()
            .add_service(EchoService)
            .serve_with_incoming(incoming)
            .await
    })
}

/// Descriptor-selected unary and server-streaming methods perform real local gRPC calls.
#[tokio::test]
async fn invokes_descriptor_driven_local_grpc_methods() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let path = directory.path().join("echo.bin");
    std::fs::write(&path, descriptor_set()).expect("descriptor writes");
    let (endpoint, server) = start_echo_server().await;
    let unary = CompiledDriverRequest::Grpc {
        endpoint: endpoint.clone(),
        descriptor_set: Some(path.clone()),
        reflection: false,
        service: "local.test.Echo".to_string(),
        method: "Unary".to_string(),
        server_streaming: false,
        credential_metadata: BTreeMap::new(),
        message: serde_json::json!({"text": "dock"}),
        timeout_ms: 1_000,
    };
    let mut unary_events = GrpcDriver::new()
        .invoke(&unary)
        .await
        .expect("unary call succeeds")
        .events;
    let unary_event = unary_events
        .recv()
        .await
        .expect("unary event exists")
        .expect("unary event succeeds");
    assert_eq!(unary_event.payload, serde_json::json!({"text": "dock"}));
    assert!(unary_event.terminal);

    let streaming = CompiledDriverRequest::Grpc {
        endpoint,
        descriptor_set: Some(path),
        reflection: false,
        service: "local.test.Echo".to_string(),
        method: "Watch".to_string(),
        server_streaming: true,
        credential_metadata: BTreeMap::new(),
        message: serde_json::json!({"text": "run"}),
        timeout_ms: 1_000,
    };
    let mut stream_events = GrpcDriver::new()
        .invoke(&streaming)
        .await
        .expect("streaming call succeeds")
        .events;
    let first = stream_events
        .recv()
        .await
        .expect("first event exists")
        .expect("first event succeeds");
    let second = stream_events
        .recv()
        .await
        .expect("second event exists")
        .expect("second event succeeds");
    assert_eq!(first.payload, serde_json::json!({"text": "run-1"}));
    assert!(!first.terminal);
    assert_eq!(second.payload, serde_json::json!({"text": "run-2"}));
    assert!(second.terminal);
    server.abort();
}

/// Dynamic gRPC invokes fixed descriptor-backed methods over a local Unix socket.
#[cfg(unix)]
#[tokio::test]
async fn invokes_descriptor_driven_grpc_over_uds() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor_path = directory.path().join("echo.bin");
    let socket_path = directory.path().join("echo.sock");
    std::fs::write(&descriptor_path, descriptor_set()).expect("descriptor writes");
    let server = start_echo_uds(&socket_path).await;
    let request = CompiledDriverRequest::Grpc {
        endpoint: format!("unix://{}", socket_path.display()),
        descriptor_set: Some(descriptor_path),
        reflection: false,
        service: "local.test.Echo".to_string(),
        method: "Unary".to_string(),
        server_streaming: false,
        credential_metadata: BTreeMap::new(),
        message: serde_json::json!({"text": "dock"}),
        timeout_ms: 1_000,
    };
    let mut events = GrpcDriver::new()
        .invoke(&request)
        .await
        .expect("Unix gRPC call succeeds")
        .events;
    let event = events
        .recv()
        .await
        .expect("response exists")
        .expect("response succeeds");
    assert_eq!(event.payload, serde_json::json!({"text": "dock"}));
    server.abort();
}

/// Descriptor loading rejects a configured streaming mode that disagrees with the method.
#[tokio::test]
async fn validates_configured_streaming_mode() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let path = directory.path().join("echo.bin");
    std::fs::write(&path, descriptor_set()).expect("descriptor writes");
    let error = load_method(&path, "local.test.Echo", "Watch", false)
        .await
        .expect_err("stream mismatch fails");
    assert!(
        matches!(error, DriverError::InvalidResponse(detail) if detail.contains("streaming mode"))
    );
}

/// Canonical protobuf JSON round-trips through a descriptor-bearing dynamic message.
#[test]
fn dynamic_json_round_trip_uses_descriptor() {
    let pool = DescriptorPool::decode(descriptor_set().as_slice()).expect("descriptor decodes");
    let descriptor = pool
        .get_message_by_name("local.test.EchoRequest")
        .expect("message exists");
    let message = decode_json_message(descriptor, &serde_json::json!({"text": "dock"}))
        .expect("request maps");
    assert_eq!(
        encode_json_message(&message).expect("response maps"),
        serde_json::json!({"text": "dock"})
    );
}

/// Explicit reflection resolves and invokes the same fixed local method.
#[tokio::test]
async fn invokes_reflection_discovered_local_grpc_method() {
    let (endpoint, server) = start_echo_server().await;
    let request = CompiledDriverRequest::Grpc {
        endpoint,
        descriptor_set: None,
        reflection: true,
        service: "local.test.Echo".to_string(),
        method: "Unary".to_string(),
        server_streaming: false,
        credential_metadata: BTreeMap::new(),
        message: serde_json::json!({"text": "dock"}),
        timeout_ms: 100,
    };
    let mut events = GrpcDriver::new()
        .invoke(&request)
        .await
        .expect("reflection-backed call succeeds")
        .events;
    let event = events
        .recv()
        .await
        .expect("response exists")
        .expect("response succeeds");
    assert_eq!(event.payload, serde_json::json!({"text": "dock"}));
    server.abort();
}
