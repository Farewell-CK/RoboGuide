# ADR-0171：原 Owned Controller 内容提交入口

- 状态：Accepted
- 日期：2026-10-23
- 前置：[ADR-0169](0169-consensus-bound-content-stream-transport.md)、
  [ADR-0170](0170-scoped-prepared-artifact-sources.md)

## 权威与部署

原 Controller 子进程在同一 supervisor 主机，通过私有 Unix socket 上的真实双向 TLS
连接。内核提供的实际 peer PID/UID 必须匹配原 supervisor spawn/PID/boot/start-tick
日志及仍未 wait 的原 incarnation，不能由请求里的 PID、成员证书或重启后新进程替代。
每次操作和 bounded source chunk 都重新检查原 epoch、transfer fence、实际 captured
TLS/current trust、原进程、原 kernel budget 与完整 target。Unix endpoint 不接受一般
成员 application RPC，也不把 infrastructure quorum 当作设备或执行许可。

显式 core constructor 使用原 network profile v0.4、Root4 和新的 member-local parent8。
parent8 保留原 Raft/producer/process/trust 语义，新增 mandatory ingress schema/head 与
逐原命令记录。parent7 或旧 production profile 不隐式升级；旧入口不能打开 parent8。
Raft Snapshot 不能导入或清空该本地操作权威。实际旧排空/升级、恢复与 runnable
composition 必须显式接线，不能因新的 core 入口提前宣称生产 HA。

## 原命令与内容

接收完整原 candidate、原实际 source writer 和原不可续预算后，先在原 parent writer
下保存完整操作身份、actual process/leaf、候选/target 和原预算，再发 Ready。即使输入
为空或没有新事件，原意图及 mandatory head 仍存在。每个新 qualified input 在原
Root4 独立创建 Controller-origin stream intent/pin 后才允许原 child 打开实际 source。
child 只从同一实际 SDK writer 的 finalized 或 ADR-0170 live Prepared guard 读取。
原 source/reader/worker 在真实 blocking scope 关闭前保留 FD、writer 和容量。

完整原 bytes/hash/fsync/receipt 之后，沿正常 Raft content Commit，并独立核对同一批
成员的 metadata 与完整 bytes，在每个 current/joint voting set 都达严格多数后才 ACK。
成员间独立传输和观察按原 transport/root 容量并行，输入内仍按序；完整 Commit 直接
保留本次实际 complete-member 证据，不能重造另一批观察来续期 child clock。原 child
预算贯穿 queue、source chunks、metadata proposal 和最终结果。新 child 预算由部署显式
声明，缺省 12 秒、支持 100 ms–300 秒，首次意图之后不可变；这不改变旧 RPC 的期限。
成员 stream 保持原 15 秒上界，握手/首帧保持 15 秒；完整 child effect 另受原预算和
300 秒 listener 上界保护。实际部署须把该声明固定到 policy identity。期限不保证工作
能完成，过期保留 Unknown，不能自动新建操作或重做。

显式 network parent7/8 的 TLS current-trust 查询采用原 inode/writer 下的独立只读
WAL snapshot，前后检查原 inode、格式和完整 trust。它不等待 application mutex，
也不读取未提交 trust；原 member writer 和原 SQL mutation 内的证书授权复核仍保留。
旧 metadata profile 的 TLS/存储含义保持，不由此增加内容提交权限。

SIGKILL 后重开入口时，必须已持有原 parent/root writer 并核对完整原 process history。
旧 endpoint 只有在实际同 user/private directory、socket type/mode/原 inode 均匹配，且
实际连接得到明确 ConnectionRefused 后才移除；活 listener、普通文件、symlink、其他
连接错误或 inode 变化均拒绝。清理 socket 不是原 child wait 或设备停止证明。
原上传 Prepared 仍不等于 Completed；收到 byte receipt 不授予 manifest、Memory use、
Node 当前可用性、Task 完成或物理停止。

原 command ID 与候选/原进程/权限/预算绑定，重试只观察原记录。缺回执、取消、进程死
或 SQL 失败保留 Unknown，不发送第二轮原 copy/Commit。只读观察可另外证明确切原
candidate 已获当前完整 quorum，但不能改写第一次不确定结果或续预算。不同显式 repair
必须保留原失败记录；不存在“checkpoint digest 相同即可视作完整提交”的降级路径。

## 验收与继续实施

必须覆盖实际 Unix peer PID 拒绝、同证书不同进程、原进程退出/替换、真实 mTLS/current
revocation、原预算/SQL/回包丢失、零输入与 mandatory head、Prepared 源、同 ID 不重做、
新旧二进制/实际 inode 和独立接收 bytes。完整 runnable child、内容路由/readiness、
joint/catchup/Snapshot/升级/恢复/GC/业务目标矩阵及原 P1–P7 仍逐项执行。
软件进程证据不构造具身 Node、设备、模型、模拟器或物理连续性。
本轮 core/文件源的实际边界和原失败见
[验收记录](../development/acceptance/2026-10-23-owned-controller-content-ingress.md)。
