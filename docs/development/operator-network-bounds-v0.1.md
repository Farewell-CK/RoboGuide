# Operator 网络边界与已提交读取

设计：[ADR-0119](../decisions/0119-bounded-operator-network-isolation.md)。这是软件入口的
资源与等待边界，设备、能力和业务拓扑由部署声明及正式注册提供。

## 配置与失败行为

Standalone 通过 `ROBOGUIDE_OPERATOR_CONNECTION_LIMIT` 配置同时连接，默认 128，范围
1–1024。Replica child 必须在每个 process profile 的 `controller_environment` 中显式
声明，环境与实际 Controller binary 进入原 application policy digest。例如：

```json
{"TOKIO_WORKER_THREADS":"1","ROBOGUIDE_OPERATOR_CONNECTION_LIMIT":"64"}
```

已有副本数据库不能只改环境和 digest 来升级；完整 F5 升级合同仍为必需。

每个实际连接在 TLS 前取得 permit，直到 handshake/request/事务/response 全部退出才释放。
满载直接关闭新 transport，没有解析命令、没有 application receipt/ACK。客户端只能在原
命令幂等与查询合同下处理不确定结果，不能把一次连接中断视为“没有副作用”。
TLS handshake、request read 和 response write/shutdown 各有原 30 秒网络预算。
response timeout 只停止该 socket I/O；不撤销已经提交的命令，不重复 Execute，也不续
任何业务预算。SQL/共识的原门禁及超时规则保持有效。

## 同步工作与当前权限

Operator 请求 future 每次 poll 前归还 multi-thread Tokio network worker，原同步授权、
writer、SQL、只读归约和 inline mutation 因而不会占住唯一网络 worker。Pending 只保留
原 future，原 socket/reactor 继续负责 I/O；不引入另一份 Runtime 或权威。
current-thread/offline 调用仍同步执行，不能拿它证明网络隔离。

每次 mTLS 请求仍真实读取原 grant 文件并检查证书有效期。完整配置与当前已提交授权
相同时，读取不等待 writer；变化仍经原 writer 二次检查、SQL Commit 后发布。
mutation 的实际 `AuthorizedRequestGate` 不变：排队后用原 TLS witness、当前文件与
已提交委托核对 principal/role/tenant/path，实际 guard 持有至 Commit 或 rollback。
只读快速路径有检查时刻，不声称外部撤销与全部在途读取瞬时线性化。

registry、identity/attestation、semantic verifier、Memory import/query/handoff、Mission
admission/resume/review 在短锁克隆后用私有 candidate 工作，Commit 后短锁发布；整个过程
保留原 writer。CAS 验证和慢 SQL 不再持有 live Controller mutex。metrics 同样先取
已提交只读快照，SQL 统计发生在短锁之外；该临时快照没有写入、资源或调度权威。

## 观察与检查

以下信号由原 permit 的真实状态产生，每秒采样；observer loss 仍按原诊断接口暴露。

| 指标 | 含义 |
| --- | --- |
| `roboguide_operator_connections_active` | 最近一秒采样的真实活动 transport，包括未完成 TLS 和慢响应 |
| `roboguide_operator_connections_limit` | 当前进程声明的连接预算 |
| `roboguide_operator_connections_rejected_total` | 原进程 TLS 前因无 permit 关闭连接的累计数 |

原固定 stage scalar 同时保留。`RoboGuideOperatorCapacityExhausted` 对五分钟新增 rejection
产生 warning；完整满载时 scrape 也可能被拒绝，已有 ScrapeUnavailable 规则负责该情况。
这些信号不证明吞吐容量或长期 SLO，也不授权增加业务并发、释放资源或恢复设备。

```bash
cargo test -p integration-server original_operator_waits
uv run python tools/quality/check_identity_processes.py \
  --output /absolute/new-proof --skip-build --async-workers 1 \
  --operator-connection-limit 4 --capacity-probe
```

进程检查创建实际软件证书和零个 Node：占满四个实际 TLS 连接、拒绝第五个、独立
Artifact listener 仍回复；真实 SQLite writer 阻塞与并发 metrics 期间 health 仍回复，
原 inotify 排队撤销拒绝第二条命令。重启、稳定 principal 幂等和降级拒绝继续检查。
