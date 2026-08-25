//! Descriptor-driven generic protobuf gRPC local driver.

use crate::local_engine::driver::{
    BoxDriverFuture, CompiledDriverRequest, DriverError, DriverEvent, DriverKind, DriverResponse,
    LocalDriver,
};
use http::uri::PathAndQuery;
use prost::Message;
use prost_reflect::{DescriptorPool, DynamicMessage, MessageDescriptor, MethodDescriptor};
use std::collections::BTreeMap;
use std::path::Path;
use std::time::Duration;
use tokio::sync::mpsc;
use tonic::codec::{BufferSettings, Codec, DecodeBuf, Decoder, EncodeBuf, Encoder, Streaming};
use tonic::metadata::{AsciiMetadataKey, AsciiMetadataValue};
use tonic::transport::{Channel, Endpoint};
use tonic::{Request, Status};

/// Invokes fixed unary and server-streaming gRPC methods using protobuf descriptors.
#[derive(Debug, Clone, Copy, Default)]
pub struct GrpcDriver;

impl GrpcDriver {
    /// Creates a descriptor-driven gRPC driver without opening a connection.
    pub const fn new() -> Self {
        Self
    }
}

impl LocalDriver for GrpcDriver {
    /// Identifies this implementation as the dynamic protobuf gRPC driver.
    fn kind(&self) -> DriverKind {
        DriverKind::Grpc
    }

    /// Loads the fixed descriptor set and invokes the configured method exactly once.
    fn invoke<'a>(&'a self, request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
        Box::pin(async move {
            let CompiledDriverRequest::Grpc {
                endpoint,
                descriptor_set,
                reflection,
                service,
                method,
                server_streaming,
                credential_metadata,
                message,
                timeout_ms,
            } = request
            else {
                return Err(DriverError::KindMismatch);
            };
            let timeout = Duration::from_millis(*timeout_ms);
            let deadline = tokio::time::Instant::now() + timeout;
            let channel = connect_channel(endpoint, timeout).await?;
            let method_descriptor = if let Some(descriptor_path) = descriptor_set.as_deref() {
                load_method(descriptor_path, service, method, *server_streaming).await?
            } else if *reflection {
                reflect_method(channel.clone(), service, method, *server_streaming).await?
            } else {
                return Err(DriverError::InvalidResponse(
                    "dynamic gRPC connection has neither descriptor set nor reflection".to_string(),
                ));
            };
            let input = decode_json_message(method_descriptor.input(), message)?;
            let output = method_descriptor.output();
            let route = PathAndQuery::try_from(format!("/{service}/{method}"))
                .map_err(|error| DriverError::InvalidResponse(error.to_string()))?;
            let mut grpc = tonic::client::Grpc::new(channel);
            grpc.ready()
                .await
                .map_err(|error| DriverError::Transport(error.to_string()))?;
            let mut tonic_request = Request::new(input);
            apply_metadata(&mut tonic_request, credential_metadata)?;
            let codec = DynamicCodec::new(output);
            if *server_streaming {
                let response = grpc
                    .server_streaming(tonic_request, route, codec)
                    .await
                    .map_err(map_status)?;
                Ok(streaming_response(response.into_inner(), deadline))
            } else {
                let response = grpc
                    .unary(tonic_request, route, codec)
                    .await
                    .map_err(map_status)?;
                let payload = encode_json_message(&response.into_inner())?;
                Ok(single_event_response(payload))
            }
        })
    }
}

/// Connects one fixed local gRPC endpoint over TCP/TLS or Unix Domain Socket.
async fn connect_channel(endpoint: &str, timeout: Duration) -> Result<Channel, DriverError> {
    let url =
        url::Url::parse(endpoint).map_err(|error| DriverError::Transport(error.to_string()))?;
    if url.scheme() == "unix" {
        #[cfg(unix)]
        {
            let socket_path = std::path::PathBuf::from(url.path());
            let connector = tower::service_fn(move |_: http::Uri| {
                let socket_path = socket_path.clone();
                async move {
                    tokio::net::UnixStream::connect(socket_path)
                        .await
                        .map(hyper_util::rt::TokioIo::new)
                }
            });
            return Endpoint::from_static("http://localhost")
                .connect_timeout(timeout)
                .timeout(timeout)
                .connect_with_connector(connector)
                .await
                .map_err(|error| DriverError::Transport(error.to_string()));
        }
        #[cfg(not(unix))]
        {
            return Err(DriverError::Transport(
                "Unix Domain Socket gRPC is unsupported on this platform".to_string(),
            ));
        }
    }
    Endpoint::from_shared(endpoint.to_string())
        .map_err(|error| DriverError::Transport(error.to_string()))?
        .connect_timeout(timeout)
        .timeout(timeout)
        .connect()
        .await
        .map_err(|error| DriverError::Transport(error.to_string()))
}

/// Loads one method descriptor through the standard local gRPC reflection service.
async fn reflect_method(
    channel: tonic::transport::Channel,
    service_name: &str,
    method_name: &str,
    configured_server_streaming: bool,
) -> Result<MethodDescriptor, DriverError> {
    use tonic_reflection::pb::v1::server_reflection_request::MessageRequest;
    use tonic_reflection::pb::v1::server_reflection_response::MessageResponse;
    use tonic_reflection::pb::v1::{
        ServerReflectionRequest, server_reflection_client::ServerReflectionClient,
    };

    let request = ServerReflectionRequest {
        host: String::new(),
        message_request: Some(MessageRequest::FileContainingSymbol(
            service_name.to_string(),
        )),
    };
    let mut client = ServerReflectionClient::new(channel);
    let mut responses = client
        .server_reflection_info(Request::new(tokio_stream::once(request)))
        .await
        .map_err(map_status)?
        .into_inner();
    let response = responses
        .message()
        .await
        .map_err(map_status)?
        .ok_or_else(|| {
            DriverError::InvalidResponse("gRPC reflection returned no response".into())
        })?;
    let descriptor = match response.message_response {
        Some(MessageResponse::FileDescriptorResponse(descriptor)) => descriptor,
        Some(MessageResponse::ErrorResponse(error)) => {
            return Err(DriverError::InvalidResponse(format!(
                "gRPC reflection error {}: {}",
                error.error_code, error.error_message
            )));
        }
        _ => {
            return Err(DriverError::InvalidResponse(
                "gRPC reflection returned an unexpected response".to_string(),
            ));
        }
    };
    let files = descriptor
        .file_descriptor_proto
        .into_iter()
        .map(|bytes| {
            prost_types::FileDescriptorProto::decode(bytes.as_slice()).map_err(|error| {
                DriverError::InvalidResponse(format!(
                    "gRPC reflection descriptor is invalid: {error}"
                ))
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    let mut pool = DescriptorPool::new();
    pool.add_file_descriptor_protos(files).map_err(|error| {
        DriverError::InvalidResponse(format!(
            "gRPC reflection descriptors cannot be resolved: {error}"
        ))
    })?;
    find_method(
        &pool,
        service_name,
        method_name,
        configured_server_streaming,
    )
}

/// Loads and validates one service method from a serialized descriptor set.
async fn load_method(
    descriptor_path: &Path,
    service_name: &str,
    method_name: &str,
    configured_server_streaming: bool,
) -> Result<MethodDescriptor, DriverError> {
    let bytes = tokio::fs::read(descriptor_path).await.map_err(|error| {
        DriverError::InvalidResponse(format!(
            "cannot read gRPC descriptor set `{}`: {error}",
            descriptor_path.display()
        ))
    })?;
    let pool = DescriptorPool::decode(bytes.as_slice()).map_err(|error| {
        DriverError::InvalidResponse(format!("cannot decode gRPC descriptor set: {error}"))
    })?;
    find_method(
        &pool,
        service_name,
        method_name,
        configured_server_streaming,
    )
}

/// Finds and validates one fixed method in a descriptor pool.
fn find_method(
    pool: &DescriptorPool,
    service_name: &str,
    method_name: &str,
    configured_server_streaming: bool,
) -> Result<MethodDescriptor, DriverError> {
    let service = pool.get_service_by_name(service_name).ok_or_else(|| {
        DriverError::InvalidResponse(format!(
            "gRPC service `{service_name}` is absent from the descriptor set"
        ))
    })?;
    let method = service
        .methods()
        .find(|descriptor| descriptor.name() == method_name)
        .ok_or_else(|| {
            DriverError::InvalidResponse(format!(
                "gRPC method `{service_name}.{method_name}` is absent from the descriptor set"
            ))
        })?;
    if method.is_client_streaming() {
        return Err(DriverError::InvalidResponse(format!(
            "gRPC method `{service_name}.{method_name}` uses unsupported client streaming"
        )));
    }
    if method.is_server_streaming() != configured_server_streaming {
        return Err(DriverError::InvalidResponse(format!(
            "configured streaming mode does not match gRPC method `{service_name}.{method_name}`"
        )));
    }
    Ok(method)
}

/// Converts canonical protobuf JSON into a dynamic message of the described input type.
fn decode_json_message(
    descriptor: MessageDescriptor,
    message: &serde_json::Value,
) -> Result<DynamicMessage, DriverError> {
    let source = serde_json::to_vec(message)
        .map_err(|error| DriverError::InvalidResponse(error.to_string()))?;
    let mut deserializer = serde_json::Deserializer::from_slice(&source);
    let dynamic = DynamicMessage::deserialize(descriptor, &mut deserializer).map_err(|error| {
        DriverError::InvalidResponse(format!(
            "gRPC request does not match its descriptor: {error}"
        ))
    })?;
    deserializer.end().map_err(|error| {
        DriverError::InvalidResponse(format!("gRPC request contains trailing JSON: {error}"))
    })?;
    Ok(dynamic)
}

/// Converts a dynamic protobuf response into canonical protobuf JSON.
fn encode_json_message(message: &DynamicMessage) -> Result<serde_json::Value, DriverError> {
    serde_json::to_value(message).map_err(|error| {
        DriverError::InvalidResponse(format!(
            "gRPC response cannot be represented as protobuf JSON: {error}"
        ))
    })
}

/// Adds ASCII gRPC metadata sourced from environment variables without exposing secret values.
fn apply_metadata(
    request: &mut Request<DynamicMessage>,
    credentials: &BTreeMap<String, String>,
) -> Result<(), DriverError> {
    for (name, environment_variable) in credentials {
        let key = name.parse::<AsciiMetadataKey>().map_err(|error| {
            DriverError::InvalidResponse(format!(
                "configured gRPC metadata name `{name}` is invalid: {error}"
            ))
        })?;
        let secret = std::env::var(environment_variable)
            .map_err(|_| DriverError::MissingCredential(environment_variable.clone()))?;
        let value = secret.parse::<AsciiMetadataValue>().map_err(|error| {
            DriverError::InvalidResponse(format!(
                "gRPC metadata value from environment variable `{environment_variable}` is invalid: {error}"
            ))
        })?;
        request.metadata_mut().insert(key, value);
    }
    Ok(())
}

/// Maps a gRPC status into a transport failure that preserves dispatch ambiguity.
fn map_status(status: Status) -> DriverError {
    DriverError::Transport(format!(
        "gRPC status {}: {}",
        status.code(),
        status.message()
    ))
}

/// Wraps one unary protobuf response in the common driver event stream.
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

/// Starts ordered forwarding of an accepted server-streaming gRPC response.
fn streaming_response(
    stream: Streaming<DynamicMessage>,
    deadline: tokio::time::Instant,
) -> DriverResponse {
    let (sender, receiver) = mpsc::channel(16);
    tokio::spawn(async move {
        forward_stream(stream, deadline, sender).await;
    });
    DriverResponse { events: receiver }
}

/// Buffers one message so the final streamed protobuf response is marked terminal.
async fn forward_stream(
    mut stream: Streaming<DynamicMessage>,
    deadline: tokio::time::Instant,
    sender: mpsc::Sender<Result<DriverEvent, DriverError>>,
) {
    let mut pending = match next_stream_message(&mut stream, deadline).await {
        Ok(Some(message)) => message,
        Ok(None) => {
            let _ = sender
                .send(Err(DriverError::InvalidResponse(
                    "gRPC server stream returned no messages".to_string(),
                )))
                .await;
            return;
        }
        Err(error) => {
            let _ = sender.send(Err(error)).await;
            return;
        }
    };
    let mut sequence = 0_u64;
    loop {
        match next_stream_message(&mut stream, deadline).await {
            Ok(Some(next)) => {
                let payload = match encode_json_message(&pending) {
                    Ok(payload) => payload,
                    Err(error) => {
                        let _ = sender.send(Err(error)).await;
                        return;
                    }
                };
                if sender
                    .send(Ok(DriverEvent {
                        sequence,
                        payload,
                        terminal: false,
                    }))
                    .await
                    .is_err()
                {
                    return;
                }
                sequence = sequence.saturating_add(1);
                pending = next;
            }
            Ok(None) => {
                let result = encode_json_message(&pending).map(|payload| DriverEvent {
                    sequence,
                    payload,
                    terminal: true,
                });
                let _ = sender.send(result).await;
                return;
            }
            Err(error) => {
                match encode_json_message(&pending) {
                    Ok(payload) => {
                        if sender
                            .send(Ok(DriverEvent {
                                sequence,
                                payload,
                                terminal: false,
                            }))
                            .await
                            .is_err()
                        {
                            return;
                        }
                    }
                    Err(encoding_error) => {
                        let _ = sender.send(Err(encoding_error)).await;
                        return;
                    }
                }
                let _ = sender.send(Err(error)).await;
                return;
            }
        }
    }
}

/// Reads one stream message while enforcing the connection-wide configured deadline.
async fn next_stream_message(
    stream: &mut Streaming<DynamicMessage>,
    deadline: tokio::time::Instant,
) -> Result<Option<DynamicMessage>, DriverError> {
    tokio::time::timeout_at(deadline, stream.message())
        .await
        .map_err(|_| DriverError::Transport("gRPC response timed out".to_string()))?
        .map_err(map_status)
}

/// Protobuf codec whose decoder is constructed with the configured output descriptor.
#[derive(Clone)]
struct DynamicCodec {
    /// Descriptor needed to instantiate each decoded response message.
    output: MessageDescriptor,
}

impl DynamicCodec {
    /// Creates a codec for one fixed response message type.
    fn new(output: MessageDescriptor) -> Self {
        Self { output }
    }
}

impl Codec for DynamicCodec {
    type Encode = DynamicMessage;
    type Decode = DynamicMessage;
    type Encoder = DynamicEncoder;
    type Decoder = DynamicDecoder;

    /// Creates a stateless protobuf encoder.
    fn encoder(&mut self) -> Self::Encoder {
        DynamicEncoder
    }

    /// Creates a protobuf decoder retaining the configured response descriptor.
    fn decoder(&mut self) -> Self::Decoder {
        DynamicDecoder {
            descriptor: self.output.clone(),
        }
    }
}

/// Prost encoder for a descriptor-bearing dynamic request message.
#[derive(Debug, Clone, Copy)]
struct DynamicEncoder;

impl Encoder for DynamicEncoder {
    type Item = DynamicMessage;
    type Error = Status;

    /// Encodes one already-validated dynamic message into a gRPC frame.
    fn encode(&mut self, item: Self::Item, destination: &mut EncodeBuf<'_>) -> Result<(), Status> {
        item.encode(destination)
            .map_err(|error| Status::internal(error.to_string()))
    }

    /// Uses tonic's bounded default buffer-growth policy.
    fn buffer_settings(&self) -> BufferSettings {
        BufferSettings::default()
    }
}

/// Prost decoder that creates each response with its configured descriptor.
#[derive(Clone)]
struct DynamicDecoder {
    /// Response descriptor used for allocation and wire validation.
    descriptor: MessageDescriptor,
}

impl Decoder for DynamicDecoder {
    type Item = DynamicMessage;
    type Error = Status;

    /// Decodes one complete gRPC message while preserving unknown protobuf fields.
    fn decode(&mut self, source: &mut DecodeBuf<'_>) -> Result<Option<Self::Item>, Status> {
        DynamicMessage::decode(self.descriptor.clone(), source)
            .map(Some)
            .map_err(|error| Status::internal(error.to_string()))
    }

    /// Uses tonic's bounded default buffer-growth policy.
    fn buffer_settings(&self) -> BufferSettings {
        BufferSettings::default()
    }
}

#[cfg(test)]
mod tests {
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
}
