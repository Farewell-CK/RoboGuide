# 副本传输容量 v0.1

适用 [ADR-0163](../decisions/0163-bounded-replica-transport-admission.md)。这是本机异步
RPC 容量隔离，原 replica v0.1–v0.3 wire/集群 identity、wrapper、Node 合同保持原义。

`controller-replica-process/v0.1` 可显式声明以下完整本地字段；省略 `transport_limits`
时使用这些默认值。它们不进入 application/cluster policy digest；成员可以有不同容量。

```json
{
  "transport_limits": {
    "inbound_connections": 128,
    "outbound_connections": 64,
    "small_frame_bytes": 8388608,
    "large_frame_bytes": 536870912
  }
}
```

这段只是现有完整 process config 的字段示意，不能单独启动服务。两个连接容量均须为
1–1024；small pool 为 64 KiB–64 MiB；large pool 为 1 MiB–2 GiB。未知字段、零值或
超范围值在打开 SQL / socket 前拒绝；支持范围还受实际平台 semaphore 上限约束。

入站在 TLS 之前持有实际 handler permit，十五秒原等待覆盖握手/读取/dispatch/响应。
所有 handler 由 listener 的 JoinSet 持有并回收。关闭 listener 请求取消其网络 handler，
后台 SQL/内容 worker 继续保留各自原预算、租约和结果，不从网络关闭推断副作用未发生。

异步收发与成员内证书、handoff 查询共享原 member budget。长度头检查及按字节 permit
先于接收 Vec；编码先有界计数，取得原容量后只分配确切长度，第二遍也不能超出原计数。
认证摘要使用相同 JSON 字节的流式 SHA-256。每个原请求处理期间保留其帧容量；实际响应
发送/接收也持有自己的原容量。失败/EOF/超时/取消回收原 permit，没有等待队列。
独立管理客户端有自己的有界 exchange 容量；同步 Controller fence 客户端保留原独立
writer/socket/256 MiB 上限，本规范没有把它列入 member 的异步池。

不超过 64 KiB 的编码帧使用独立 small pool，其余使用 large pool；任何单帧仍不得超过
原 256 MiB wire 上限。合法 wire 帧也可能因本地池当前无容量被拒绝。large pool 满不
占用 small pool，但连接容量耗尽仍会拒绝小帧连接。这里没有 OS/RSS/GPU 强制限额、
CPU 隔离、抗所有 DoS 或长期 quorum/SLO 保证。

开启现有 private `metrics_address` 后，状态文件包含实际 `transport` snapshot，指标使用
固定名称 `roboguide_replica_transport_*`：active/limit、原始帧 bytes、sampled high-water、
入站/出站/帧拒绝和原 handler 超时计数。旧 status 没有该字段时只报告
`observation_present 0`，不会补零伪造历史。进程重启计数重新开始，不续原操作预算。

容量拒绝不产生 Raft/应用 receipt。实际 Commit/transfer 已送出后断连、超时或响应池
不足仍可能已提交；使用原操作身份和既有只读查询对账，禁止换 ID 重做来猜测成功。
出现容量拒绝时先核对实际 active、原 deadline、连接来源与部署容量；保持有界拒绝，
不要通过取消原历史、无限排队或放开 wire cap 恢复服务。调整配置后受控重启 member，
原 SQL、transfer/process history、预算和应用权限保持原样。

实际无 Node 门禁：

```bash
cargo build --locked -p controller-replication --example replica_transport_probe
uv run --frozen python tools/quality/check_replica_transport_processes.py --output /tmp/rg-transport-check
```

输出目录必须全新。显式 `--legacy-probe` 可追加实际冻结旧二进制的基线，不提供时标记
不可用。探针使用三个实际 infrastructure members/真实 mTLS、单 async worker，未创建
Controller、RoboGuide Node、设备、模型或模拟器。它不代替完整 online 内容 ACK、
supervisor 升级/接管、Node/Mission failover 和其余 P1–P7 验收。
