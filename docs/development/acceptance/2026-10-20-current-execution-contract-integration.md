# 新增执行合同与整改主线的集成验收

本项将远程 `codex/e1-perception-contract-composition` 固定快照
`b50d9e313b0660c9cc4ee1c5bca53538fa0b1de6` 合入整改主线
`6ca3ebb37fca940626adc0d68755fc38a0534f0f`。保留原六条提交和人类作者，不改动原工作树。
共同祖先为 `008aa8a203c8790d5e92fe9cd50e2d00c30743cb`；三方补丁共 41 个文件，
没有删除或回退已验收的 Controller、Memory、证书、恢复与运行期修订权威。

## 合入的行为

- 执行规划证据 v0.2 明确公布部署支持的 coupling modes、DAG、Context relations 和
  session-stable Actor binding；不向模型公开 Node inventory。
- 只读 validate-plan 和提交共用具体 code/path；区分模式、能力、资源保留冲突与不确定
  outcome。模型保留真实合作需求、原目标和非持久观察语义，不能为通过校验删除约束。
- Extension Conformance v0.4 保留原配置 v0.9–v0.13 和实际协商 Node Contract。
  v0.8+ 原 provider continuity 语义保留；未来版本失败，不降级到旧报告掩盖能力。
- Habitat 可选 Local How v0.12 只消费原缓存与原 invocation；暂停受原步数预算约束，
  新 acquisition 只重置该 endpoint 的原导航命令完成 latch。原 arrival 计算、world、
  sensor、原模型选择与官方 PDDL 仍由原所有者提供。本次没有运行 Habitat 或模型。

## 组合证据

Controller wrapper v43 / Bridge inner v23 / Node journal v13 保持原义。
实际 Controller binary SHA-256 为
`d71c5b78895696fdcad9d3aaf19505fa57bd7aea8292b6a42f78245d0e7e283a`，
Node binary 为 `6128db7f0f2519bbeed14fcc69491cb13b7b3c037679bfe170a1b10d5ce2d73d`。

| 结果 | 实际核验 | SHA-256 |
| --- | --- | --- |
| `/tmp/rg-latest-branch-revision-20261020-a/RESULT.json` | 原 child Cancel 后仍存活、提前切换拒绝、实际 exit 后手动/预审自动修订、独立目标、取消/重启、真实 v42 迁移/旧 writer 拒绝 | `e2c2c4183d1a53aa72cecf23fbeff37cdfce8ff2f36707dc0fd2ef9d24a095b1` |
| `/tmp/rg-latest-branch-memory-20261020-a/result.json` | 两个实际软件 Node、文件 import/CPU/独立 verifier、原 revoke/reclaim、SQL 故障与实际原生告警 | `a4ba176aa98f423848f019d507c0b9b7ea59c22e90c004630d370086632a0474` |

两个进程检查均通过并回收 owned processes；devices/models/simulators 均为零。
实际 Habitat Python 3.9 的 canonical parsing、SQLite、loopback HTTP 兼容检查通过，
日志 `/tmp/rg-latest-branch-habitat-python39-a.log`。用实际 Node CLI 只读编译原 v0.13
配置，报告 v0.4 / config v0.13 / `roboguide.node.v0.12`；没有连接 Controller 或设备。
结果 `/tmp/rg-latest-branch-config-v013-conformance.json`。

质量检查日志以 `/tmp/rg-latest-branch-` 为前缀。Rust workspace 共 915 passed /
2 explicit ignored；Python 共 2697 passed / 1 skipped。Clippy all-targets 和 private
Rustdoc 的 `-D warnings`、Ruff/format、Mission 与 evaluation strict mypy、函数文档、
diff 检查以及严格 MkDocs 构建均通过。
本次仅关闭这六条新增提交的组合验收，不关闭 F0/F4–F10、自动新 MI 候选或 scoped
Memory 迁移，也不声明总体 SoftwareComplete、物理停止或实验结果。
