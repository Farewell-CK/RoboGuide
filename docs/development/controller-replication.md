# 可选 Controller 三副本软件部署

ADR-0083 的当前实现是三个静态成员的 trusted-loopback 软件 profile，默认不启动。
每个 `controller-replica` 有独立 FULL-synchronous SQLite log/state machine，OpenRaft
负责共识；它仅在当前 epoch 能确认多数时拥有一个 `integration-server` child。
child 仍使用既有 Control/Runtime/Orchestration，不复制一套资源或 Task 决策。

```mermaid
flowchart LR
  N[Node Service 与 durable journal] -->|正式 facts| C[leader Controller child]
  C -->|完整 candidate 与 exact base| L[leader replica / OpenRaft]
  L --> F1[follower durable metadata]
  L --> F2[follower durable metadata]
  F1 -->|quorum| L
  F2 -->|quorum| L
  L -->|接受且同 epoch| C
  C -->|local Commit 后 ACK| N
  C -->|再次检查 epoch 的 Execute/Cancel| N
  F1 -.->|promotion 后重建并恢复 Unknown| C
  A[独立 Artifact/Memory 内容] -.-> C
```

事务先复制完整 authority checkpoint 和新增连续 event metadata，校验前一个 snapshot
digest/tail；quorum 接受后才 local SQL Commit、安装 candidate、ACK。Execute/Cancel 各自
检查原 epoch；父进程消失或 quorum 丢失时，child watchdog 关闭自己的公开 routes。
该检查是软件发送授权点，不是 Node 端分布式 fencing token，也不是硬实时停止保证。
已经发送的工作保持原 Local EAIOS/Control stop-proof 规则。

切换先从 accepted history 原子重建私有 `controller.sqlite3`，然后普通 startup restore
清空 routes/leases/session anchors，保留 reservations、Actor/registry watermark、
原 receive time、Mission hold/priority/aging、stop/retry evidence，nonterminal Runtime
恢复 Unknown。Node 重新登记和 replay 状态不授权旧 Execute 重放。已经 quorum 接受但
本地 Commit/response 丢失的批次从 accepted view 收敛。

## 配置与启动

先构建 `cargo build --locked -p controller-replica -p integration-server -p roboguide-node`。
为三成员分别提供 closed JSON，以下为第一成员示例；二、三成员改变 `replica_id`、
自己的 storage directory 和三个公开地址，保留同一 members、cluster、application
schema 和 byte-bound policy。路径必须显式且 storage/binary 为绝对路径。

```json
{
  "schema": "roboguide.controller-replica-process/v0.1",
  "replica": {
    "schema": "roboguide.controller-replica/v0.1",
    "cluster_id": "local-controller",
    "replica_id": 1,
    "members": {
      "1": "127.0.0.1:32001",
      "2": "127.0.0.1:32002",
      "3": "127.0.0.1:32003"
    },
    "application_schema": "roboguide.controller-checkpoint/v25",
    "application_policy_digest": "REPLACE_WITH_POLICY_DIGEST"
  },
  "storage_directory": "/absolute/private/replica-1",
  "controller_binary": "/absolute/RoboGuide/target/debug/integration-server",
  "grpc_address": "127.0.0.1:32101",
  "http_address": "127.0.0.1:32201",
  "artifact_address": "127.0.0.1:32301",
  "controller_environment": {"ROBOGUIDE_CHECKPOINT_STORAGE": "full"}
}
```

`controller-replica --policy-digest /absolute/replica-1.json` 计算实际 binary SHA、明确的
Controller environment 和可选 `actor_placement_path` / `physical_entity_registry_path`
文件 bytes 的摘要；将结果写入三份同 policy 的配置后再启动
`controller-replica /absolute/replica-1.json`。child 清空继承环境，只有允许的 protocol、
batch、checkpoint-storage、admission 变量可明确提供。当前 profile 不接入可变 feasibility/
verifier 文件消费者；它们需要独立复制配置/source-history 决策。变更 binary、registry
或 policy 不支持在线更新，也不支持带旧 history 的无迁移升级；升级需要独立的可信迁移
发布 gate，不能用空 cluster 丢弃已有物理 ownership。
已有 standalone 数据库不会自动迁入 cluster；集群快照在 standalone 启动时会拒绝。

Node config v0.7 保留 `server_endpoint`，可加 `fallback_server_endpoints`（最多两个，
全部 distinct、startup validated）。Node 在 session loss 后按配置顺序旋转，保持同一
NodeId、durable command journal 和 Local EAIOS handle；旧 v0.2-v0.6 配置不能启用非空 fallback。
HTTP/Artifact 客户端使用当前 leader 的显式地址；本 slice 不提供自动 client redirect。
private `status.json` 仅供本地观察，不授予写入或资源 authority。

SIGINT 请求 supervisor 停止自己拥有的 child 和共识；异常 parent 退出由 child watchdog
保守关闭。不要共享 replica 的 storage directory；既有 writer lock 与 supervisor lease
防止第二 writer。每个 supervisor 仅管理自己的 child，不扫描或终止外部服务。

## 证据与边界

`uv run python tools/quality/check_controller_replication_processes.py --output /fresh/output`
启动隔离的实际三 replica/Controller/two Node processes 和合成 HTTP Local EAIOS。
它覆盖 minority startup、leader death、Unknown/ownership/policy/journal 保留、metadata
rejoin、old route closure、standalone downgrade 和 live majority loss，保留所有失败。
没有 MI、Provider、Habitat、真实设备或 E6。unit/integration tests 另外验证 upstream
完整 storage conformance、snapshot/reopen、错 base、stale epoch、quorum loss 和 quorum
接受后 response/SQL Commit 失败；既有 production restore 测试验证 holds/停止预算。

2026-10-12 第一轮 debug binary hashing 导致 30s startup timeout，失败完整保留；改为
bounded streaming SHA 并启用 SHA 加速后，第二轮真实进程检查通过，两个原 local Execute
没有增加，45 rows 的 accepted metadata prefix 重新汇聚。第三轮在 quorum 恢复瞬间
复用旧 HTTP 地址遇到 connection reset，原结果完整保留。只读探针改为每次重新解析
leader 并等待新 Node 活性证据后，第四轮全部通过：44 rows metadata prefix、old route
closure、standalone downgrade、live majority loss/restore，local Execute 仍为 2。
记录位于仓库外 `adr-follow-up-audit-2026-10-12/replica-processes-{1,2,3,4}/`；
后续存储完整性修订还需在最终源码重跑进程检查，不把旧 binary 结果当作新 binary 证据。

当前只支持三成员同机 trusted-loopback；无跨主机认证/网络发布、动态 membership、
unbounded archive 或物理 continuity 认证。每个 checkpoint 至多 64MiB，新增 batch
至多 8192 events，完整 event metadata history 至多 64MiB，snapshot/frame 至多 256MiB；
超过界限 fail closed。Artifact blobs、provider-local Memory 内容不复制；目录可发现
manifest 不证明新 leader 已有相应 bytes。完整 event reducers 仍未实现，复制完整
snapshot/history 不是每 N events 丢弃不可恢复事实。E6 持续暂停。

最终存储修订增加 event/checkpoint digest 与 snapshot byte identity、真实 SQL rollback/
corruption 回归。此前最终源码进程检查在正常工作阶段发生早期 child fence，原结果保留在
`replica-processes-final/`。核对 OpenRaft 源码确认 quorum-read RPC TTL 等于 heartbeat：
本软件 profile 现固定 500ms heartbeat、1.5–3s election，quorum probe 外层仍最多 2s；
Node transport lease、health/readiness TTL 保持原规则。supervisor 使用四个 runtime workers。
最后 `replica-processes-final-2/` 在实际 binary
`0959093165c3324f18183f411df00988655bed208366dbe05bbb5a644791a155` 全部通过：
45 rows exact metadata prefix 汇聚，两个原 local Execute 保持不变，quorum 恢复使用
新 registration 的 Reachable 证据。676 Rust tests（另 2 个既有 ignored）、Clippy、
Rustdoc、完整 Python 检查和严格站点构建通过；这些不是 E6 或生产规模/硬实时结果。
