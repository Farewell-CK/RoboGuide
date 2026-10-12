# ADR-0086：Node 身份与增量 mTLS 安全档案

- 状态：Implemented as an opt-in transport profile
- 日期：2026-10-12
- 范围：Node Protocol gRPC 会话与 Node 侧连接配置

## Decision

默认开发模式继续使用明确受信任的 loopback。启用安全档案时，Integration Server 使用
双向 TLS，要求客户端证书，并将证书 DER 的 SHA-256 digest 绑定到声明的 `NodeId`。Hello
到达后，证书身份必须先通过绑定检查，才允许进入 Register、路由或应用事实队列。

Node Service 通过 `ROBOGUIDE_NODE_MTLS_CLIENT_CERT`、`ROBOGUIDE_NODE_MTLS_CLIENT_KEY`、
`ROBOGUIDE_NODE_MTLS_SERVER_CA` 和 `ROBOGUIDE_NODE_MTLS_DOMAIN` 建立 mTLS 客户端。服务端
通过 `ROBOGUIDE_NODE_MTLS_SERVER_CERT`、`ROBOGUIDE_NODE_MTLS_SERVER_KEY`、
`ROBOGUIDE_NODE_MTLS_CLIENT_CA` 和 `ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS` 启用对应档案。
配置不完整时拒绝启动；没有配置时保留原 loopback 兼容路径。

Controller replica RPC 另外支持可选的共享 transport secret。secret 不在线传输，只用于
绑定 profile、target、sender 和 command 的 SHA-256 envelope digest；缺少或错误 digest
在进入 OpenRaft 之前拒绝。

## Boundaries

- 证书绑定证明连接调用者身份，不证明 Node 的物理状态、Local EAIOS 行为或任务满足。
- loopback 兼容模式不是跨主机认证模式；生产跨主机部署必须使用安全档案和显式证书轮换。
- epoch、session、sequence、owner 和 Control/Runtime authority 仍是独立检查。

## Validation

离线测试覆盖未知 Node、digest 不匹配、完整配置校验和 replica envelope 认证。未启动 E6，
未使用真实设备或 Habitat。
