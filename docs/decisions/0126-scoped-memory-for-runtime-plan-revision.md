# ADR-0126：运行期计划修订的原属组 Memory 交接

状态：Accepted，实施中。

## 问题

ADR-0120/0121 的新组必须在旧组完整停止和撤权后由 Control 创建。ADR-0108 的普通
handoff 要求两端都已经绑定，不能用于尚不存在的新修订组。跳过 scope 校验、预绑定新组
或 Review 后改写 selector 都会改变原许可，不能作为修复。

## 决定

显式 `roboguide.runtime-plan-revision/v0.3` 增加 `authorize-memory`，由实际
recovery-admin 在原 candidate 的输出、claim 和 Review 之前提交。输入包含原 selector、
完整不可变 child、目标组、原 registry/revision；冻结原属组、membership、原计划版本、
tenant、主体、leaf 和原预算。不创建 Group、资源或 import，也不发布 manifest。
child 仅改变 selector、Group scope 和创建时间，保留原 owner/provider、实际 bytes、
类型/媒体和全部生产 provenance。普通 generic Memory 首先支持；typed Spatial 仍须保留
原完整 map 来源，不能借此变成通用 alias。

每个 candidate 最多 16 个 child，总许可 metadata 有界。相同命令只返回原 receipt，
冲突或来源漂移拒绝。候选和模型上下文公开这些声明；最终完整 candidate 必须直接引用
原 child selector。Review 摘要包含许可摘要；输出、claim 或 Review 后不能追加/替换。
没有声明的原属组输入继续拒绝，不自动降低为 global/discoverable。

切换复核原 Control membership、tenant、Handoff/child Import+Read 策略、当前实际主体、
原 parent、Review、原预算和完整 stop/revoke 集合。全部满足后，原 authority transaction
记录完整 lineage、发布 child 事件并正常 supersede/Create Group；并不预选 Node。
子清单仅在 Commit 后安装 catalog。新 Task 的正常 Match/Commit/Bind、独立 preparation、
实际 import/probe/current-use 与最终 Execute 校验全部保留，旧组不会自动恢复。

Artifact 的 parent 引用例外只授予该 child 在该修订目标组的实际原 import，每个 chunk
重新经过当前 child 权限；普通 CAS alias、未停止 source、无关 Group/tenant 不获得权限。
后续 Task 仍取得自己的 import/preparation；不重用上一 Task 的身份或原预算。

共享 Controller wrapper v45 fences 旧 writer；v44 及之前保留原 authority、调用 nonce、
预算和时间，不得携带新的交接许可。新的协议不改变 Node Contract v0.12/config v0.13、
journal v13、Bridge v23 或 event codec v28。

## 验收与限制

通过实际软件 Node、mTLS、CAS、原 CPU 子进程退出、独立复算和重启验证修订链路；
覆盖未授权、提前切换、来源/策略/Review 漂移、SQL 回滚、旧 writer 和后续 Task 身份。
自动生成可消费预先声明的 child；生成前必须已具备显式许可，不能在未知 provider 调用后
改写其输入。软件进程退出不代表设备停止。完整 Spatial、全局引用回收、在线 HA 和跨域
工作按原计划继续，不能由本项推断总体 SoftwareComplete。
