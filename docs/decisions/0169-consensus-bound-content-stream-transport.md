# ADR-0169：共识绑定的内容流传输

- 状态：Accepted，实施中
- 日期：2026-10-23
- 前置：[ADR-0168](0168-original-bounded-replica-content-streams.md)

## 决策

网络内容能力使用显式 Replica v0.4、实际 stream Root4 与新的 parent storage v7。
普通 metadata v0.1–v0.3、direct storage v4–v6 和原预算/历史保持原义，拒绝隐含
新 member owner、网络 stream 或完整内容 ACK。新 parent 的 mandatory marker 和
实际 root identity 在零业务操作时也存在；历史库不自动升级/清空/改 profile。

仅显式新网络构造器接收实际已经持有 writer 的 Root4 及声明的 bootstrap member
owner map。实际 self root 必须匹配 self owner；首次 membership 把完整 endpoint/
owner 纳入 Raft。之后以确切 current committed membership 为准，禁止通过另一个
局部 map 改写既有 member，禁止由相同数字 ID、地址或证书推导存储 owner 连续性。

内容接收只接受实际有效 mTLS 上的原当前 leader、原 profile/epoch 和确切 membership。
每次流冻结完整目标、qualified input、原 peer leaf/source root 和实际 receiver
预算。原 writer 下校验之后，durable stream intent/pin 先于 Ready，Ready 先于源
payload 打开。重复 ID 只返回原观察；不发第二个 Ready 或创建第二次载荷读取。

传输使用 64 KiB 有界块和有界队列，独立于 Raft application/Snapshot metadata。
唯一 blocking worker 保留实际 writer/root/FD/capacity/原时钟，异步网络取消只关闭其
通道，不能提前释放仍运行的工作。原权限/epoch/membership/证书有效性按块检查；
锁竞争拒绝时保留 Unknown，不能通过锁反向依赖阻断 Raft worker。原 deadline、TLS/
frame/response 上界继续生效，不以无界 reader、后台任务或 payload-size buffer 绕过。

应用接纳前，本地原内容与每个远端的实际原 byte receipt 分别验证；Raft 元数据
接纳后，再独立观察确切原 accepted metadata 与完整 qualified bytes。只有同一批
实际 members 在每个 current/joint voting set 都达到严格多数才返回完整 ACK。
两个不相交多数、同 hash 的其它 owner、同 member ID 的其它 root 和旧 epoch 均不可
借用。Readiness 和原 child 启动也须独立检查本地完整原投影和字节。

原 Controller 的完整候选/recovery/origins、真实 source writer 和 Prepared FD 读
能力必须单独接线；公开 DTO、声明或旧裸 digest 不提供源授权。原 child 软件停止/
进程 drain、应用取消/停止和物理安全权威继续分别成立。

## 实施与验收

先接新的 parent/member/profile 与实际 TLS stream，再接完整候选、同 voters ACK、
catchup/Snapshot、原 child/Prepared source、内容路由及 recovery/升级/回退。
Runnable supervisor 在其完整合同接好前明确拒绝生产 v0.4，不因 core 私有构造器可
运行而宣布完整 HA。原 P1–P7/F0–F10/R01–R20 的其余条件继续必需。

验收使用任意声明的真实 infrastructure members、实际 mTLS/TCP/SQL/CAS/owned CPU
进程、原故障/回执丢失/期限/旧格式/当前与 joint 配置；不创建设备/能力 inventory，
不启动模型或模拟器。后续业务链仍须真实 software Node 文件工作与独立原目标证明。
这些事实不建立跨主机 RTO/RPO、设备停止、硬实时或物理连续性。
