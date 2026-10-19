# 2026-10-20 main 分支集成与软件组合验收

状态：本工作包通过。整体 F4 和 F5–F10 继续实施；不宣称 SoftwareComplete。
设备、模型和模拟器均为 0，没有启动实验或 E6。

## 基线与交付

main 原为 `5285a92a6af8bf4a99bb0852b5cdb80728c9144c`。本次组合整改头
`25380295a267fb4c5f2f948558460fd1b141f95f` 与执行故障闭环头
`d15543f4ec2dc99ebe565e319e141c0bf17b433b`；后者包含 MI 部署校验头
`b4f1b1275a546f310f61f9b79ac0bc4bb353209a`。在隔离工作树解决 18 个文件的文本冲突，
并复核组合后的权威；没有带入原工作树的未跟踪实验、会议文件或独立项目。

[ADR-0111](../../decisions/0111-branch-integration-and-authority-version-fences.md) 保留
Memory tags 8–10，新增 generation tag 11，显式采用 Node Contract v0.12/config v0.13/
catalog v0.15、共享 wrapper v41 和 Node journal v12。停止分支的历史 ADR 顺延为
ADR-0109/0110，Memory ADR-0106/0107 不变。原停止分支的歧义 v32 checkpoint 含进程退出
权威时拒绝自动迁移；没有用同号 schema 推断兼容性。

## 实际发现并修复的组合故障

1. 两分支重复占用协议字段和版本号。Memory preparation 与 Execute 现在保留相同的
   非零原始 generation；旧 metadata 不会升级旧合同，缺失/更改 generation 与旧 epoch
   在准备或执行副作用前拒绝。旧 Memory invocation digest 不追加隐藏字段。
2. 确认 Cancelled 后的非成功 Task outcome 曾进入自然失败收尾，释放交接源 Group。
   实际交接失败证据保留在 `/tmp/rg-integration-handoff-20261020`；专项回归也先复现了
   allocation 从 Bound 变空。修复要求当前原始 Failed attempt 且没有 handoff binding
   保留，Cancelled 继续保留 Mission hold；真实 Failed 的正常收尾仍通过。
3. Artifact 最后一个应用引用退出后，子进程继承 descriptor 曾使合法重启报 writer busy。
   `/tmp/rg-artifact-lease-reproduction-20261020.log` 保存实际子进程的失败复现。共享 lease
   在所有 store clone 和上传释放后显式 unlock；仍有任意上传或 clone 时拒绝第二 writer。

## 验收结果

| 检查 | 结果 | 本地证据 |
| --- | --- | --- |
| Rust workspace | 869 passed，2 项原有 ignored，27 个 suite | `/tmp/rg-main-integration-rust-composition-final.log` |
| Python 全部配置的 testpaths | 2575 passed，1 skipped | `/tmp/rg-main-integration-python-verified.log` |
| fmt / Clippy `-D warnings` / private Rustdoc `-D warnings` | 通过 | `/tmp/rg-main-integration-clippy-composition-final.log`、`/tmp/rg-main-integration-rustdoc-composition-final.log` |
| Ruff format/check、分模块 strict mypy、函数文档检查 | 通过 | `/tmp/rg-main-integration-*-composition-final.log` 及分模块 mypy 日志 |
| 真实原进程退出、两软件 Node 与 Controller 重启 | 通过；原 Failed/Completed 不改写，worker 未退出时不确认 | `/tmp/rg-int-process-stop-final/RESULT.json` |
| 双 Group Memory 交接 | 通过；3 个实际 file jobs，2 个独立 verifier，2 个 SQL 回滚窗口，重启预算不变 | `/tmp/rg-int-handoff-complete-final/result.json` |
| 原期限到期、策略变化、待交接 Controller 重启 | 三项均通过；manual、ownership 保留、无目标计算 | `/tmp/rg-int-handoff-expiry-final`、`/tmp/rg-int-handoff-policy-final`、`/tmp/rg-int-handoff-restart-final` |
| 独立 JSON Schema | 实际命令/配置/接收证明通过，10 个结构破坏拒绝 | `/tmp/rg-int-handoff-schemas-final.log` |
| 实际旧二进制与前向升级 | v40→v41 零 Node 升级通过；旧 Controller 拒绝非空/零事件 v41，旧 Node 拒绝 journal v12；全部 authority 表逻辑 digest 不变 | `/tmp/rg-int-upgrade-final.log` |

Memory 的四个实际进程配置均保存源码/二进制与配置 digest、原 context/provider 证明及
`owned-process-exits.json`。核对实际 PID 全部被回收，原始证据不以重新导入或另一个 worker
替代。成功路径 source 仍 held；目标只在 explicit commit 后执行，后续 Task 使用新成员代际。

最终二进制 SHA-256：

| 二进制 | SHA-256 |
| --- | --- |
| integration-server | `b2da93cc60f810e84bb3e9ab0b92f7b29cb7b34249ad926eb69e2f1095fd6661` |
| roboguide-node | `8106c920723ba936e34bd3cd8b3b44833a73382e34a5fc0e7506e1850fa96fa9` |
| controller-replica | `ec0b9ba48f62e29304958bd5ab5efcd3d7ba236477c4828a9927e89ff720caff` |

## 复现入口与边界

```bash
cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
RUSTDOCFLAGS='-D warnings' cargo doc --workspace --no-deps --document-private-items
uv run pytest -q
uv run python tools/quality/check_process_stop_evidence.py --output /tmp/unique-process-stop
uv run python tools/quality/check_memory_preparation_processes.py --handoff-case complete --output /tmp/unique-memory-handoff
uv run python tools/quality/check_memory_handoff_schemas.py --directory /tmp/unique-memory-handoff
uv run python tools/quality/check_memory_release_upgrade.py --directory /tmp/unique-memory-handoff --old-controller /path/to/archived-v40-controller --old-node /path/to/archived-v11-node
```

`--handoff-case` 还支持 `expiry`、`policy-change`、`controller-restart`。输出目录须为新的
私有目录；旧二进制必须是实际归档文件。本次实际旧 Controller/Node SHA 分别为
`ecc03f886f2982a167d36f25dfb7019ee3ec20257492a2b70bb3d5548bf4c4f8` 和
`cc250c141b6de7264597dd5e5d647bfc69cfbb1be039603943abf04bda615357`。

软件线程/进程、TLS、CPU、文件、SQL 和 CAS 不提供设备停止、物理交接、硬实时或硬件身份
证明。52 个合并前远程分支及未合入项见[完整核对清单](../roboguide-integration-status-2026-10-20.md)。
独立根历史项目、Android 专项产品和排除的实验分支继续保留；完整引用/GC、HA 数据面恢复、
资源/执行协调、运行期计划、跨域 transport 和运维总验收仍按原计划完成。
