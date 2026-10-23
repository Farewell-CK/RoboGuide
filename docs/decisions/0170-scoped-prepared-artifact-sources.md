# ADR-0170：Prepared Artifact 的作用域读取

- 状态：Accepted
- 日期：2026-10-23
- 前置：[ADR-0169](0169-consensus-bound-content-stream-transport.md)

## 决策

Artifact 的 Prepared 元数据可能先于最终 CAS publication 提交。完整内容复制必须能
读取实际原 staging bytes，不能提前把 Prepared 宣布为 Completed、泛化为公开 CAS
availability，或通过重新执行上传/Task 解决递归提交。新增能力仅用于实际原 owned
writer 内部的复制数据源，不授予 HTTP 内容、Memory use 或执行权限。

原 upload 在完整 size/hash/fsync 检查后显式建立非序列化的 prepared source guard。
guard 固定原 upload ID、原 writer owner/descriptor、实际 staging FD device/inode、
digest/size；registry 隶属于同一原 writer lease，各 SDK clone 共享，最多 128 个
未结束作用域。弱引用过期可移除，不能淘汰实际 live source 以腾出空间。

读取句柄使用真实只读 FD 与独立 position，不改变原 uploader 的写 cursor；每块
最多 64 KiB，检查原 source revoke、writer identity、文件 identity/size 和 GC fence。
actual guard/readers 仍存在时拒绝继续 append；finalize 只可使用原 digest/size。
guard Drop 或 upload abort/drop 撤销原读取能力，但不宣布实际 reader 已结束。实际
reader 关闭 FD 之后才释放其 writer ownership/写冻结。丢失 guard 不从 CAS 或历史
记录自动重建；旧未持有 guard 的上传行为保持。

公开 `open_artifact` 与 Artifact HTTP 保持仅 finalized 的原义。独立内部 source
lookup 可读取确切 finalized bytes 或一个实际 live prepared source；未注册、已撤销、
wrong size/owner 或被替换的实际 source 拒绝。Prepared source guard 不是持久 grant，
不进入 checkpoint、Node Protocol、Raft metadata 或恢复清单，不产生 owner alias。

Controller HTTP 在原 Prepared 元数据提交前持有 guard，持续到该次原 finalize 路径
结束；SQL 失败时保留原上传和原结果，不把实际 guard/FD 活性伪造成持久完成。接收
Replica 的实际持久意图、bytes/hash/fsync/receipt 仍独立成立。下阶段 owned child
入口还必须绑定实际 Unix peer PID/原 supervisor journal、真实 mTLS/原预算与完整原
candidate；本项不启用 runnable supervisor 的内容模式。

## 验收

实际 SDK 文件测试覆盖 guard 前不可读取、guard 后 public CAS 仍不存在、多个独立
reader position、原 append 冻结、drop/revoke 与 reader 退出分离、abort、finalize、
writer clone lifetime、实际 wrong file/size、容量拒绝和 SQL 失败重试。真实上传及
复制进程组合必须独立观察原 bytes 与原记录；不创建设备、模型、模拟器或物理证明。
原 P1–P7、完整 owned child/Prepared ingress、回收/恢复与整体完成条件继续必需。
实际文件、HTTP/SQL 与独立 native 进程证据见
[本轮验收](../development/acceptance/2026-10-23-prepared-artifact-sources.md)。
