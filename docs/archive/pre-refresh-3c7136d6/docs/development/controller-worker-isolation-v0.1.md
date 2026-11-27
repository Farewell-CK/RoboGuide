# Controller 同步事务与网络 worker 隔离

日期：2026-10-20。设计见
[ADR-0118](../decisions/0118-synchronous-application-worker-isolation.md)，
实际证据见[验收](acceptance/2026-10-20-worker-and-transfer-liveness.md)。

## 当前执行边界

timer 和 Node fact 在完整原同步事务前归还 multi-thread Tokio 的网络 worker，之后仍在
同一个原 writer 下处理输入、私有 candidate、SQL/共识 Commit、live projection 和 outbox。
没有新命令队列、第二份权威或并发 timer batch。原 Node durable ACK 仍在 apply 成功后。
原 receive time 不随等待修改，实际 dispatch 仍用 fresh clock，重启不续预算。

Artifact 的 route 身份/授权/audit、完整上传/发布/观察，以及每个实际内容 chunk 的
writer/权限/文件操作采用同一同步边界。异步 socket/body 保持网络操作。`block_in_place`
让 Tokio 提供替代网络 worker；它不缩短 SQL/共识工作，也不跳过当前证书和 owner 检查。
current-thread offline callers 明确保留同步行为，不能用它们证明多线程 worker 隔离。

部署可为 standalone 明确设置 `TOKIO_WORKER_THREADS`。Replica child 清除继承环境，
需在每个显式 process profile 的 `controller_environment` 中声明：

```json
{"TOKIO_WORKER_THREADS":"1","ROBOGUIDE_CHECKPOINT_STORAGE":"full"}
```

允许 1–64，其他值拒绝；环境字节与实际 binary 进入既有 application policy digest。
现存 HA store 不能通过只改 digest 或 schema 升级，仍要求完整 F5 升级合同。
线程数是部署资源配置，不是保证吞吐、SLO 或设备硬实时的参数。

## Leader transfer 临时状态

原实现成功转移后没有恢复旧 source 的 automatic heartbeat/election；该 source 再次成为
目标时可能无法参与正常选举/维持空闲 leader。取消及部分 timeout 也可能遗留禁用。
每个 source 的 transfer 现在串行，temporary guard 在正常完成、错误、deadline 和 future
取消时都恢复原正常 timers，包含已经变成 follower 的路径。

这只恢复 Raft 自身正常行为，当前 quorum/epoch 继续是写入与交付门禁。取消等待不保证
网络上已发送的目标请求没有效果；调用者仍须观察当前 leader/epoch，不能把取消当成
“transfer 没发生”。该修复不等于完整 durable transfer barrier、动态 roster 或滚动升级。

## 可复现检查

```bash
cargo test -p integration-server original_timer_waits_for_real_writer_while_single_network_worker_serves_tcp
cargo test -p controller-replication repeated_transfer_back_to_original_source_preserves_idle_quorum
cargo test -p controller-replication cancelled_actual_transfer_wait_restores_original_automatic_heartbeat
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /absolute/new-file-proof --async-workers 1 --authority-recovery --content-reclamation
uv run python tools/quality/check_replica_certificate_processes.py \
  --output /absolute/new-replica-proof --skip-build --async-workers 1
```

前者用实际单 worker、真实 mutex、SQL 与 TCP；后两者是实际三引擎/SQL/socket/选举。
文件链使用正式配置接入的两个软件 Node、实际文件计算和独立 verifier，零设备/模型/模拟器。
三副本 profile 不创建 Node。Observer loss、未知/stop/Memory 仍按原权威处理。

后续 [Operator 网络边界](operator-network-bounds-v0.1.md)将完整请求 poll、连接预算与
response I/O 接入，并缩短 registry/Memory/verifier 等 live mutex 的持有范围。
长期 SLO、其他 listener 的完整容量与跨主机压力、原恢复/升级矩阵仍按 F10/F5 收敛。
不能把本项当成所有历史 HTTP 故障或整体整改已关闭。
