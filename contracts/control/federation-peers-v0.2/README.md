# Federation peer profile v0.2

显式部署合同，见 [schema](schema.json) 与 [ADR-0217](../../../docs/decisions/0217-explicit-federation-request-budget.md)。

`schema_version=roboguide.federation-peers/v0.2` 必须同时携带
`budget_scope=intent-publication-and-peer-io`。每次原请求预算为 100–30,000 ms，
从 SQL-admitted 原 kernel clock 开始；落盘排队、TLS、网络及 peer 请求共用原期限。
到期前没有完成 publication 的工作不得打开新连接。丢失回复保留 Unknown，只读对账
不得重发原副作用。原事务预算、nonce、root epoch 和 cleanup deadline 不因新配置续期。

v0.1 仍只接受 100–10,000 ms，且不携带新字段。配置历史须连续 revision、保留原域和
stable principal；接受 v0.2 后禁止回退 v0.1。旧闭合 decoder 必须拒绝新字段。

`peers` 是部署侧明确声明的实际服务端点和凭证路径，无默认域、Node 或设备。
运行时额外验证 SocketAddr、本地域存在、实际证书/CA 字节和 leaf pins；配置摘要
不代替当前 TLS、授权、quorum 或 original budget。私钥内容不进入应用 checkpoint。
