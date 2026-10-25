//! Actual pre-TLS connection ownership, bounded admission and fixed process observations.

use ports::{DiagnosticHandle, DiagnosticKind, DiagnosticStage};
use std::io;
use std::pin::Pin;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::task::{Context, Poll};
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{OwnedSemaphorePermit, Semaphore};
use tokio::time::Sleep;
use tokio_stream::Stream;
use tonic::transport::server::{Connected, TcpConnectInfo};

/// Original initial-traffic and TLS handshake bound; it grants no application permission.
pub(crate) const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(5);
/// Transport inactivity after first traffic; it never renews the independent Node lease.
const IDLE_TIMEOUT: Duration = Duration::from_secs(15);

/// Independent listener namespaces keep metrics bounded without peer or Node labels.
#[derive(Clone, Copy)]
pub(crate) enum Listener {
    /// Actual Node HTTP/2 transports, including incomplete mTLS connections.
    Node,
    /// Actual Artifact HTTP transports, including response and payload delivery.
    Artifact,
}

/// Parses an explicit local deployment limit before listener binding or service admission.
pub(crate) fn configured_limit(
    name: &str,
    default: usize,
    maximum: usize,
) -> Result<usize, String> {
    match std::env::var(name) {
        Err(std::env::VarError::NotPresent) => Ok(default),
        Ok(value) => value
            .parse::<usize>()
            .ok()
            .filter(|v| (1..=maximum).contains(v))
            .ok_or_else(|| format!("{name} must be an integer in 1..{maximum}")),
        Err(_) => Err(format!("{name} must contain valid UTF-8")),
    }
}

/// Shared actual permit authority; sampled diagnostics never replace this semaphore.
struct Inner {
    /// Actual connection lifetime capacity, acquired before TLS.
    semaphore: Arc<Semaphore>,
    /// Original configured process bound.
    limit: usize,
    /// Actual excess transports refused before application work.
    rejected: AtomicU64,
    /// Fixed observation namespace.
    listener: Listener,
    /// Bounded best-effort diagnostics, independent of application authority.
    diagnostics: DiagnosticHandle,
}

/// Cloneable process-local admission boundary, independent of Control resources or Node inventory.
#[derive(Clone)]
pub(crate) struct Connections(Arc<Inner>);

impl Connections {
    /// Establishes a validated explicit bound and publishes its initial observation.
    pub(crate) fn new(
        listener: Listener,
        limit: usize,
        diagnostics: DiagnosticHandle,
    ) -> Result<Self, String> {
        if !(1..=1024).contains(&limit) {
            return Err("listener connection limit must be an integer in 1..1024".into());
        }
        let result = Self(Arc::new(Inner {
            semaphore: Arc::new(Semaphore::new(limit)),
            limit,
            rejected: AtomicU64::new(0),
            listener,
            diagnostics,
        }));
        result.observe();
        Ok(result)
    }

    /// Acquires before handshake; rejected sockets never spawn an application or TLS worker.
    pub(crate) fn acquire(&self) -> Option<ConnectionPermit> {
        match Arc::clone(&self.0.semaphore).try_acquire_owned() {
            Ok(permit) => {
                self.observe();
                Some(ConnectionPermit {
                    permit: Some(permit),
                    owner: self.clone(),
                })
            }
            Err(_) => {
                let _ = self
                    .0
                    .rejected
                    .try_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
                        Some(value.saturating_add(1))
                    });
                self.observe();
                None
            }
        }
    }

    /// Samples fixed names without retaining addresses, certificates, tenants or per-request labels.
    fn observe(&self) {
        let stages = match self.0.listener {
            Listener::Node => [
                DiagnosticStage::NodeConnectionsActive,
                DiagnosticStage::NodeConnectionLimit,
                DiagnosticStage::NodeConnectionRejections,
            ],
            Listener::Artifact => [
                DiagnosticStage::ArtifactConnectionsActive,
                DiagnosticStage::ArtifactConnectionLimit,
                DiagnosticStage::ArtifactConnectionRejections,
            ],
        };
        let values = [
            self.0
                .limit
                .saturating_sub(self.0.semaphore.available_permits()) as u64,
            self.0.limit as u64,
            self.0.rejected.load(Ordering::Relaxed),
        ];
        for (stage, value) in stages.into_iter().zip(values) {
            self.0
                .diagnostics
                .value(DiagnosticKind::Http, stage, value, None);
        }
    }
}

/// Owns one original connection permit until actual socket handling ends.
pub(crate) struct ConnectionPermit {
    /// Taken only on actual Drop, before sampling released capacity.
    permit: Option<OwnedSemaphorePermit>,
    /// Original process-local capacity and observation context.
    owner: Connections,
}

impl Drop for ConnectionPermit {
    /// Releases actual capacity before reporting the new observational sample.
    fn drop(&mut self) {
        self.permit.take();
        self.owner.observe();
    }
}

/// Actual TCP stream with original lifetime capacity and a finite initial protocol deadline.
pub(crate) struct BoundedTcpStream {
    /// The actual accepted kernel socket; tonic retains its real connection identity.
    stream: TcpStream,
    /// Capacity stays held through TLS, HTTP/2, streaming responses and actual close.
    _permit: ConnectionPermit,
    /// First 24 transport bytes must arrive under the original acceptance clock.
    initial_remaining: usize,
    /// Never renewed by partial input; TLS independently keeps its complete handshake deadline.
    deadline: Pin<Box<Sleep>>,
}

impl Connected for BoundedTcpStream {
    type ConnectInfo = TcpConnectInfo;

    /// Preserves tonic's exact TCP/TLS metadata types and actual certificate witness extraction.
    fn connect_info(&self) -> Self::ConnectInfo {
        self.stream.connect_info()
    }
}

impl AsyncRead for BoundedTcpStream {
    /// Applies one nonrenewing initial bound before delegating real reads to the original socket.
    fn poll_read(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        if std::future::Future::poll(this.deadline.as_mut(), cx).is_ready() {
            return Poll::Ready(Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "original Node transport initial or idle deadline expired",
            )));
        }
        let before = buf.filled().len();
        let outcome = Pin::new(&mut this.stream).poll_read(cx, buf);
        let count = buf.filled().len().saturating_sub(before);
        this.initial_remaining = this.initial_remaining.saturating_sub(count);
        if count > 0 && this.initial_remaining == 0 {
            this.deadline
                .as_mut()
                .reset(tokio::time::Instant::now() + IDLE_TIMEOUT);
        }
        outcome
    }
}

impl AsyncWrite for BoundedTcpStream {
    /// Writes only through the original kernel socket without creating another connection scope.
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.get_mut().stream).poll_write(cx, bytes)
    }

    /// Delegates flushing while retaining the original connection permit.
    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.get_mut().stream).poll_flush(cx)
    }

    /// Delegates actual shutdown; capacity remains owned until the stream drops.
    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.get_mut().stream).poll_shutdown(cx)
    }

    /// Retains the actual socket's vectored-write support for HTTP/2 payloads.
    fn is_write_vectored(&self) -> bool {
        self.stream.is_write_vectored()
    }

    /// Forwards original vectors without an unbounded intermediate allocation.
    fn poll_write_vectored(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bufs: &[io::IoSlice<'_>],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.get_mut().stream).poll_write_vectored(cx, bufs)
    }
}

/// One original listener stream; excess connections are closed before tonic's TLS acceptor.
pub(crate) struct Incoming {
    /// Actual owned listener bound by the composition root.
    listener: TcpListener,
    /// Original process-wide capacity independent of per-connection stream limits.
    capacity: Connections,
}

impl Incoming {
    /// Binds no new address and preserves the already validated actual listening socket.
    pub(crate) fn new(listener: TcpListener, capacity: Connections) -> Self {
        Self { listener, capacity }
    }
}

impl Stream for Incoming {
    type Item = io::Result<BoundedTcpStream>;

    /// Yields after a bounded rejection batch so saturated transports cannot monopolize a worker.
    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let this = self.get_mut();
        for _ in 0..32 {
            let stream = match this.listener.poll_accept(cx) {
                Poll::Pending => return Poll::Pending,
                Poll::Ready(Err(error)) => return Poll::Ready(Some(Err(error))),
                Poll::Ready(Ok((stream, _))) => stream,
            };
            if let Some(permit) = this.capacity.acquire() {
                if let Err(error) = stream.set_nodelay(true) {
                    return Poll::Ready(Some(Err(error)));
                }
                return Poll::Ready(Some(Ok(BoundedTcpStream {
                    stream,
                    _permit: permit,
                    initial_remaining: 24,
                    deadline: Box::pin(tokio::time::sleep(HANDSHAKE_TIMEOUT)),
                })));
            }
        }
        cx.waker().wake_by_ref();
        Poll::Pending
    }
}
