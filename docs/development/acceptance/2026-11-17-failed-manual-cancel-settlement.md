# Failed attempt 的人工取消结案修复

日期：2026-11-17，Asia/Shanghai。

## 结论与范围

VERIFIED：这是可复现的取消收尾死锁，代码修复及相关软件回归通过。
人工结算现在同时接纳 Cancelling Mission 当前同代的 Cancelled 或 Failed attempt。
原 Failed、停止记录、记录摘要、期限及管理员证据均保留；正常取消收尾释放 allocation。
AutomaticNoEffects 仍只接受 Cancelled，Failed 本身不会触发安全占用释放。

验证基线是 ThinkPad 当时干净的 main：
`c17d07f901555ff24c68ee3e3f0eb5fa5e23fcc9`。
仅在 `/home/duchaoke/rg-lab/wt-failed-cancel-20261117` 修改、编译和运行测试，
使用私有 target、低优先级、最多两个编译任务。未修改现场主 checkout、重启服务或启动
第二套 Controller。现场 Controller/Mission Service 原 PID 377498/382268 保持运行。

## 复现与修复

旧实现只接受 RemoteExecutionStatus::Cancelled，导致当前 Failed attempt 的管理员
结算被拒绝。Mission 的 Cancelling 分支跳过普通失败收尾，SafetyStop 又持续阻止
取消资源释放，因此没有可用的结案路径。冷恢复还会拒绝已结算的 Failed。

修复仅修改 `application/safety_stop/settlement.rs` 的两个校验：

- `reconcile_manual` 接纳当前 Cancelled 或 Failed，保留原管理员、Mission、attempt、
  generation、摘要和显式证据检查。
- `validate_restored_settlement` 按结算依据检查终态：人工允许 Cancelled/Failed；
  AutomaticNoEffects 继续要求 Cancelled。

没有修改普通 outcomes、resume、Node journal 或资源释放机制。
人工结算仍只授权取消收尾，不产生物理停止确认、resume 或 handoff 权限。
原 ADR-0232 和 AGENTS.md 已补充这一维护边界。

## 实际验证

本报告的测试结果来自 ThinkPad 独立 worktree，使用固定 Rust 1.99.1 和 locked 依赖。

| 检查 | 结果 |
| --- | --- |
| 旧代码 + 回归测试 | 9 通过、2 失败，exit 101；直接复现原 cancelled-only 拒绝信息 |
| 修复后 task_settlement | 11 通过 |
| safety_stop | 18 通过 |
| stop_recovery | 14 通过 |
| Domain all-targets | 66 通过 |
| Node service all-targets | 150 通过 |
| reconcile_rejected_dispatch example | 1 通过 |
| workspace Clippy，all-targets/all-features，warnings denied | exit 0 |
| cargo fmt --all -- --check | exit 0 |
| git diff --check | exit 0 |

修复后合计 260 项测试通过。新增或扩展回归确认：

- 没有安全声明时，终态 Failed 及普通 outcomes 仍保留取消占用。
- 管理员显式结算后，Mission 变为 Cancelled，真实 Control allocation 清零。
- attempt 保持原 Failed；原 ManualEscalation 记录、期限和未确认状态保持不变。
- Cancelling 和最终 Cancelled 两个阶段的 Runtime/Mission 检查点均可重新载入。
- 非管理员、错误租户、错误摘要/generation、未确认请求、缺失证据和冲突命令被拒绝。
- 公共 HTTP handler 的 SQLite 写入失败回滚、成功持久化及精确命令重放通过。
- 活跃/Completed attempt 和非 Cancelling Mission 仍不能人工结算。
- no-effects 观察不能自动结算 Failed；Cancelled 专属自动结算不能移植到 Failed 恢复。

HTTP 测试使用生产 handler、tokio duplex、合成认证身份及真实私有 SQLite。
检查点测试调用实际 Runtime/Mission 反序列化及 SafetyStop 恢复验证；没有重启现场服务。
这不等同于真实 mTLS 握手、Controller/Node 全进程或硬件停止验收。

原始日志、补丁和执行脚本位于 ThinkPad：
`/home/duchaoke/rg-lab/failed-cancel-evidence-20261117/`。
Windows 副本位于 `C:/workspace/DEAIOS/lab/rg-failed-cancel-settlement-20261117/`。

两份源码按 Git 的 LF 内容比较一致；ThinkPad 实测文件的 SHA-256：

```text
eae32854d0fd639d4f4b5c9f753c7af57c207dfc86087ea7f569b56fac723362  application/safety_stop/settlement.rs
d2c604626a3db8c5de37cad8b3359d6ea0324326185c2bcf4164a36bda292b6f  tests/task_settlement.rs
```

## 尚未执行

UNKNOWN：没有部署此修复，也没有对用户报告的原 Mission 调用 reconcile、resume 或 cancel。
原现场任务是否已由其他操作者结案，本轮未重新查询，不能声称现场资源已恢复。
真实全进程及旧二进制回退门禁未运行；本轮遵守不在 ThinkPad 另起 Controller 的限制。

附带的 pre-dispatch Unknown 是另一条恢复路径：`unknown_attempts` 直接来自 Runtime
当前 Unknown slots，不根据 Mission cancel 隐藏。仅有取消请求和空 resource_ids 不能
证明没有本机执行；本补丁不删除、转换或自动结算该 Unknown，需另取原始证据核查。
