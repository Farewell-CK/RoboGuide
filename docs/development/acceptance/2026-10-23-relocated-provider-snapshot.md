# 恢复后原 provider Snapshot 退休验收

唯一分支仍为 `codex/nonrelease-remediation-closure`，接续 `ab6ab46`，实现
[ADR-0158](../../decisions/0158-relocated-original-provider-snapshot-abort.md)。
此前 `a0e74d0` 的[完整 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39827808789)、
[文档 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39827808737)，以及
同步 main 文档后的 `ab6ab46` [文档 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39829150227)
均成功。没有新开分支或将非发布整改提前合回 main。

## 实现边界

当前 profile v0.16 与完整原 Snapshot profile 分别校验，实际两份 profile 的全部
writer/root/use/四 SQL inode 租约取得后，核对两个实际 provider instance 和完整原
SQL request，包含原 operation/profile digest、component、destination、command、
kernel budget 和每个原 generation-1 pin。已 published/released 的 copy 继续要求
原 verify/reconcile，不能因 destination 缺失降为未发布。Replica/online authority
由该离线入口拒绝。

只在当前 provider 的既有原 abort 事务中同时退休原 record 与各原 pin。v0.1 receipt、
SQL v4/writer v0.2、Controller wrapper v52 和 Node 合同不改变；独立历史源、原
request/budget 与 partial 保留。原 retry 核对同一已有 receipt，复制/unlink/服务
启动均为零。这个入口是既有原 abort 的完整恢复接线，不授予新的 read/use/import。

## 实际门禁与反例

证据根为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`。
`check_snapshot_reference_processes.py --relocated-abort` 由真实 provider/CLI 创建
实际 import 和 Snapshot，在原 SQL intent 已提交时真正 SIGKILL copier 并取得
原 `wait=-9`。随后完整备份/冷恢复保留该原 active pin，而非合成未知结果。

| 原记录 | 实际范围 |
| --- | --- |
| `provider-snapshot-relocated-contract-final` | 原 v0.8 profile/SQL v4 的真实 copy、SIGKILL/wait、原调用只读拒绝；冷恢复到新 root/当前 v0.16，已发布的另一 Snapshot 精确 reconcile，不改待退休原 request；独立新 response schema 与原持久 receipt 区分 |
| 同项双 writer / SQL gate | 原 source provider 活跃和当前 provider 活跃各自拒绝；两个实际 wait 后才可继续；真实 SQL trigger 拒绝 abort 时原 state/pins 一起回滚，独立读两份 SQL 保持原样 |
| 同项退休/重启/cold | 原 instance/request/digest/pin 核对后仅当前原事务退休；原 receipt 精确 retry；未改动的 v4 provider 代码实际重新启动接受同一身份，后续真实 v0.16 backup/verify/restore 保留完整 aborted history，独立历史 source 不变 |
| `provider-snapshot-artifact-regression` | 当前完整 CLI 同时完成既有 MI parent/child 释放、实际 SQL/unlink 故障、source GC、已删 bytes 后 Archive、旧 v51 拒绝及完整冷恢复；0 新 Nodes |

`provider-snapshot-relocated-01` 已完成原退休和 provider 重启，但后续 v0.16 backup
因完整目录校验漏认可 `active-map/active-map.sqlite3` 的必要父目录而失败，不能计为
cold 完成。修复只接受显式数据库/内容树的结构祖先；单元 counterexample 在 bundle
新增旁系目录并重算 manifest digest 仍拒绝。没有删除原目录证据或改写失败结果。
`provider-snapshot-relocated-02` 验证修复后的完整链路；最终记录另保留最新 checker
identity。旧字段名中的 v4 指存储合同，最终字段明确为 unchanged-v4-provider restart，
不冒称运行了另一份旧 executable。

专项 9 项实际 SQL/文件单元检查覆盖错误原 profile/digest/instance、缺原 pin、已发布
destination、SQL fault、nested-SQL 目录反例，以及同 root 只改 profile 时与原 abort
工具的精确 receipt 兼容；它们不充当真实进程证明。全 recovery 定向 **142 passed**，
strict mypy **344 files**、Ruff/format、函数文档通过。
首轮全量 Python **3117 passed、0 skipped、502.851 秒**，包含实际 Python 3.9 标准库
桥接；它在最后目录校验修复前已启动，新增反例与当前 recovery 定向另保留，不能
把该早先全量写成最终代码结果。最终完整同 SHA 以 CI 为准；文档构建独立保留。
没有改 Rust 源码，前一代码基线的 978 项 Rust 结果不被
重写成此次新运行。

## 继续工作

本项完成 provider 原未发布 Snapshot 的恢复后释放接线；已发布 copy 仍由原验证/对账
路径处理。N1 下一项为 Raft Snapshot/online Replica producer 及其各自内容/引用
生命周期，N3 要求实际 bytes 与 metadata 的当前 voting 多数（joint 两组多数）。
N2 typed-map 整链、N4–N7 原条件及 GC hot-journal 显式维护继续必需。
本项实际门禁为 0 Nodes、0 设备、0 外部模型、0 模拟器，不证明物理连续性或 SoftwareComplete。
