# ADR-0091：跨主机 replica mTLS 与统一恢复观察

- 状态：Implemented for software process validation
- 日期：2026-10-13

## 决策

`ReplicaConfig.tls` 为 replica RPC 提供 CA、certificate、private key 和 peer DNS name。
非 loopback member 必须配置该 profile；listener 强制校验 client certificate，并把 peer leaf
certificate digest 与 `sender` member identity 做 exact mapping，client 同时校验 server
certificate 的 CA/DNS，并在发送 application frame 前绑定 target member 的 exact leaf digest。
本地 member 证书也在启动时核验，member leaf 必须互不重复。legacy shared-secret envelope
仍可作为额外边界，profile identity 不含
机器本地路径。supervisor 将证书路径只注入自己拥有的 Controller child，禁止从父环境隐式
继承。

`tools/quality/check_controller_replication_processes.py --tls` 运行同一三副本/两 Node 软件
矩阵，覆盖 minority startup、leader death、old epoch route closure、Unknown/ownership/
journal 保留、rejoin、SIGSTOP 多数派损失和恢复；输出保存在指定临时目录。该证据是软件进程
和合成 Local EAIOS，不是 E6、Habitat、真机或物理 continuity 证明。

Controller HTTP 增加 `GET /v1/recovery`，按 `Ready`、`ReconciliationRequired`、
`ManualIntervention` 汇总 Unknown attempts、Group recovery 与 Mission Holds，并公开固定恢复
顺序：restore checkpoint、清除 process-local session anchors、重新注册/取状态、保留 Unknown、
reconcile commitments、人工批准歧义继续。它不自动 Execute、release resource 或替换 Node。
复查修订为 recovery view v0.2：exact attempt ID 与 correlation 分开，active pending/manual
事项与 completed recovery history 分开，固定 sequence 明确标为操作指引。身份负向回归与
视图语义见 [ADR-0092](0092-reliability-review-authority-and-archive-fences.md)。

## 边界

成员仍是静态三副本，未实现动态 membership、证书轮换编排、跨站点容量/延迟证明或物理停止。
Node Protocol mTLS 与 replica mTLS 是不同身份域；HTTP role map 也不等于 Mission semantic
authority。

同一临时 PKI 的 operator HTTP smoke 通过 mTLS `/healthz` 握手；TLS matrix 输出为
`/tmp/roboguide-replica-tls-nonloopback-20261013g/result.json`，binary SHA-256 为
`73cf1c4ea3ea09200795362f427c02b0028e37f7c3d73f581a2f4e5afe07a7d0`。
上述旧 binary 的 health smoke 不证明 mutation authorization，也不代表复查修订后的新 binary
已经验证；新证据与限制以 fault matrix 和复制部署文档为准。
