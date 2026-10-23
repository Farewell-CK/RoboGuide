# ADR-0163：副本传输的有界准入

- 状态：Accepted，实施中
- 日期：2026-10-23
- 前置：[ADR-0118](0118-synchronous-application-worker-isolation.md)、[ADR-0160](0160-original-content-producer-journal-and-interruption-reconciliation.md)

## 决策

副本 listener 在 TLS 握手、请求分配前，非阻塞获取本进程的连接 permit。
已准入的 handler 从握手到结果发送一直占用原 permit；超时、EOF、错误和取消均释放。
listener 拥有并回收全部 handler，关闭 listener 取消其网络等待，不推断任何已经提交的
Raft、SQL 或后台工作未发生。已有独立存储 worker/租约保留自己的实际生命周期。

原 wire 单帧上限 256 MiB、profile identity、版本、成员及证书授权保持原含义。
对原始编码帧另设进程内总容量：不超过 64 KiB 的帧使用独立 small pool，其它帧使用
large pool；长度检查与 permit 先于 Vec 分配。异步收发共享实际本地 budget；编码先
计算有界长度，取得容量后再分配。decoded 请求在处理期间保留对应帧 permit。拒绝不排队、
不创建 authority receipt；发送失败和取消仍可能对应已提交工作，必须按原协议只读对账。

`ReplicaTransportLimits` 是显式本地部署容量，默认入站 128、出站 64、small pool 8 MiB、
large pool 512 MiB，连接范围 1–1024、small 64 KiB–64 MiB、large 1 MiB–2 GiB。
实际 supervisor process config 可声明 `transport_limits`，旧配置省略时取有界默认值。
本地容量不加入集群/application policy digest，不赋予成员、Node、资源或执行权限；
副本 profile 和 checkpoint 不借此取得新 authority。有效原协议帧也可能因当前容量不足
被拒绝。small pool 只能隔离帧容量，连接耗尽、CPU/OS/RSS 和网络 DoS 不被它保证消除。

本地只读 snapshot 与既有 private metrics 报告配置、实际 sampled active/high-water、
拒绝和超时计数，无 request/member/tenant 等动态 label。指标不是 lease/quorum/可用性证明。

## 验收

先保留旧实际进程对慢 TCP/大长度头的行为。使用真实配置和真实 CA/leaf、独立 TCP/TLS
进程、单 async worker、实际三 infrastructure members 检查连接/帧拒绝、slow/EOF/超时/
关闭释放、大帧 pool 饱和时小帧实际响应、选举/heartbeat 与原权限拒绝。
未启动 Controller、RoboGuide Node、设备、模型或模拟器；不得因此关闭全 online HA。
全 listener/I/O、supervisor replacement、同一 voters 内容 ACK 与完整 P1–P7 继续必需。
