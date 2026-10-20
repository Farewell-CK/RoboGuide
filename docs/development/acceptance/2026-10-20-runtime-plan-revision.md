# 运行期修订、原目标与停止门禁验收

设计：[ADR-0120](../../decisions/0120-versioned-runtime-plan-revision.md)。接口与上界见
[运行期合同](../runtime-plan-revision-v0.1.md)。本项交付版本化候选/Review、预审替代、完整
原停止/撤销门禁和独立目标完成 gate。自动新 MI 候选及 scoped Memory 迁移继续实施，
完整 F8 和其余非发布阶段没有提前关闭。

## 实现与真实边界

Orchestration 保留原计划/Group/接受时间和版本链；已满足 Task 定义及 Control 事实逐项
保留，不重新 Execute。新 Task ID 不得曾被使用，相同 operation/parameters 的已满足
副作用不能只改名重做。Control 保留唯一承诺权，Runtime 保留原 attempts/intents。

原 policy、实际 TLS leaf/稳定主体/tenant/原 grant、候选、Review 与原 kernel 预算先入库。
持久 whole-Group stop 和 Memory revoke 先于新 Group；未知、超时、证据或权限变化保留
原所有权。旧 Group 的 Execute 永久被拦截；普通 resume、dispatch 和 recovery 不能
绕过待完成修订。显式 Mission 取消在同一事务结束相应修订状态。

仅保持 goal 文本不足以判定替代计划正确：Control 独立冻结原 goal/context/source，
Orchestration 安装目标 gate。所有当前 Task 满足后仍保持 Running，只有独立当前
goal evidence 才能完成 Mission。input 绑定 current version/Group/完整 attempts，
并在完整请求到达时固定预算；排队、CAS、重放和重启都不能刷新。final-negative 与
Unknown 不伪造 execution failure 或成功。原已完成结果保留其原始时钟和 receipt。

wrapper v43 / inner v23 / event codec v28 分别封住外层应用、独立 Bridge 与事件写入。
实际旧 v42/inner v22 的零 Node 待执行 Mission 升级，保留原 plan、accepted_at 与 admission
receipt；不自动创建 goal permissions。新权威的旧 wrapper/inner 伪装被拒绝。

## 实际进程验收

最终 Controller SHA-256：`447a543cd6b68b389c2821234d81061ed3a3f1fcbd4655db6ec19257045cf902`。
实际 Node journal v13 binary：`2a7ae5639da8c9736f6f6c2d8d1a91a15d20c37834861bcd7e7ce941cedffabe`。

执行命令：

```sh
.venv/bin/python tools/quality/check_plan_revision_processes.py \
  --output /tmp/rg-runtime-revision-processes-20261020-d --skip-build \
  --old-controller /tmp/rg-pre-revision-integration-server-20261020
```

一次实际 enrolled software Node、四个实际 owned POSIX children，exit codes 为
`[-15, 7, 0, -15]`。Cancel 接收后原 child 仍活着，提前 commit 被拒绝，所有权保留。
真实 exit/独立 safe-state 后手动修订成功；下一原 child 实际 exit 7 后，精确预审替代
自动切换。最终 child 实际读取文件并计算 SHA-256，独立另一个进程重新读取并验证。
当前 Task 已完成时 Mission 仍 Running，proposer 发布目标证据被拒绝；实际独立 producer
的目标 evidence 才完成 Mission，重放返回原 receipt。

普通 resume 在全部原停止门禁已满足后仍被明确拒绝；第二个 Mission 在原 child 活着时
保持 Cancelling，实际退出后才成为 Cancelled，其未应用 revision 同事务关闭。多次真实
Controller restart 保留原 budget、completed/cancelled 状态和全部四个原 attempts，目标
Group 没有在取消时执行。设备、物理实体、model/provider calls 和 simulator 均为零。
这个软件 backend 的验收 profile 明确每 Group 一次原调用，不替代多角色、设备或模型验收。

| 目录 | 核验 | RESULT SHA-256 |
| --- | --- | --- |
| `/tmp/rg-runtime-revision-processes-20261020-d` | 原停止、两种修订、独立目标、普通 resume 拒绝、取消/重启、真实 v42→v43/inner v22→v23 迁移及旧 writer 拒绝 | `24bf8bf090b43cf7da3a07cbc50f4d1a373112fd60a8753ba8bb38f7d5b4d7d4` |
| `/tmp/rg-revision-identity-20261020-a` | 单 worker/真实 TLS/四连接容量、SQL 排队撤销、当前身份/原主体/重启；零 Node facts | `532bc887eb4189281be10bc5a2a728d8bb302de938aadd0e764cd41feff308ec` |
| `/tmp/rg-revision-file-recovery-20261020-a` | 双软件 Node、真实文件/import/CPU/独立 verifier、原 revoke/reclaim、重启、两次 SQL 故障与原生告警 | `63763e45c77e766ad507246028873777e95c3887970f8d99e0acf3d2199f84a6` |
| `/tmp/rg-revision-certificates-20261020-a` | 三实际 Replica、同一成员 leaf/CA 迁移、Abort、原 store restart 与 owned-process cleanup | `df275f824182f7de179dac55dbeadb556b81d69a3bad6b084a002efb6dc2f90a` |
| `/tmp/rg-revision-cold-recovery-20261020-a` | 同一软件身份的六 SQL/39 tables/原十条换证回执、真实 CAS 新目录恢复和三个实际 leader 读取 | `2691415065d3376bc57133721c4ad18f24c55aa6f094ceb02df4846c3168f9b0` |

## 反例与检查

开发中的较早 F8 binary `06cf73586699cfe56d1b241ed9e5c719df5cce2bcd6391769f07a6ff7dbcf040`
以相同实际用例检查，普通 resume 返回 200，破坏原 switch hold。原结果在
`/tmp/rg-runtime-revision-resume-old-negative-20261020`，日志为
`/tmp/rg-runtime-revision-resume-old-negative.log`。这属于合并前 F8 组合反例，不能归因到
尚无 F8 的 main。最终同项返回 409，保留原意图并允许原 switch 正常完成。

旧正式 v42 binary `9b8bf8bca2a8b56a88e9ba4d60c3cbe2841c8dcea93e4c73278fb6997a1d7b2a`
实际生成迁移源并被新库拒写；原文件和进程日志保留。A/B/C 早期软件证据保持原样，最终
以包含完整原版本迁移和新门禁的 D 为准。

Rust workspace：910 passed / 2 explicit ignored；Clippy all-targets `-D warnings`、
private Rustdoc `-D warnings`、format 和 diff 检查通过。Python 全量 2626 passed /
1 skipped；strict mypy、Ruff、format 与函数文档检查通过。继承、反重复、原 goal 来源/
序号/过期/撤销和内外层伪装另由明确标记的 unit fixtures 验证，不当作设备证据。

之后继续实现真正的新 MI work tickets/原 provider-call SQL/Review 循环、原 Group Memory
的显式转移及其组合故障路径。其他 F0/F4–F10 必需项仍按原总计划推进；没有设备不能成为
未实现软件的替代解释。本记录不声明总体 SoftwareComplete 或设备停止/物理连续性。
