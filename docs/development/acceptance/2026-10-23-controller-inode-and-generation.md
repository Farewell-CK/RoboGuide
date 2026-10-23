# Controller 实际 inode writer 与存储代际验收

- 日期：2026-10-23
- 唯一分支：`codex/nonrelease-remediation-closure`
- 原基线：`b6fe5ff9f8bb65d76fd582c83a738e1e1da0fc71`
- 决策：[ADR-0161](../../decisions/0161-controller-inode-writer-and-storage-generation.md)
- 证据根：`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`

## 原缺陷与实现

`controller-hardlink-baseline-01/result.json` 保留两个真实旧 Controller 的 PID、
健康、相同 SQL device/inode、不同硬链接/CAS 与实际 native wait -15。两者都能启动，
原路径锁没有排除第二个 writer。旧 binary SHA-256：
`ede7c9a51778790b6e44b6f4667026ea6145bba2e173035fb0a44cb55fe544b6`。

所有 runnable Controller 现在由 `SqliteEventLog` 的实际 managed connection 持有
canonical sibling 与 SQL inode 双租约，最后一个 clone 的实际 SQLite close 先于
显式 unlock。legacy admission/迁移用原生 DELETE/exclusive 排空旧连接；只取得
flock 不代表旧连接已不存在。

显式 `ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA=1` 原子保留 raw events，使用私有写表
与确切只读 legacy view/旧 index fence；零事件、无 checkpoint 也拒绝旧 writer。
mandatory format/table/view/columns 缺失不自动补建。已接受 v1 的重开使用持久 fence
与双租约，独立 read-only observer 不阻止普通重启。legacy 缺省仍是兼容合同，
不声明持久 downgrade fence。副本 child 配置须闭合该选项并计入原 policy digest。

Recovery v0.17 显式声明 Controller 的 v1 或 legacy 与实际 Artifact v3/v5 格式，
冻结实际 Controller SQL inode/sibling，保留完整 v0.16 原引用/内容/预算检查。
旧 profile 与旧 CLI 拒绝 v1/新 profile；restore 不启动服务，不刷新原预算。
Controller wrapper v52、事件 codec、Node 合同没有因文件格式升级而改名。

## 实际证据

`controller-storage-native-05/result.json` **通过**，包含 15 次实际 Controller
启动检查，另保留实际 clone/只读 observer/empty/upgrade 工具进程和旧生产进程退出。
硬链接竞争、原旧进程未退出、原 SQL exclusive lock 占用、缺 marker/view、以 legacy
打开 v1 都拒绝；同 ID 没有产生设备或执行事实。两个真实旧 binary 的 v1/empty
启动均 native exit 1，错误为 `views may not be indexed`。

原 quota producer 由独立真实进程生产、实际 wait 0 后再迁移；完整原 SQL rows 保持。
实际原 clone 保留租约，释放后另一入口可启动。独立 read-only observer 在新 Controller
健康/配额查询/退出全程存活，其实际 wait 0，原配额 revision 2 和空 Node inventory 保留。
实际 empty 进程不创建 checkpoint。所有 Controller 的原退出码分别记录，不把 -15
称为设备停止或 Local EAIOS 的 stop certificate。

`controller-storage-original-archive-upgrade-01/result.json` **通过**，补充真实旧
binary/mTLS 上传、实际三条原 event rows、原 pending Archive 与真实字节的完整代际
升级。升级后的原事件、原 intent/checkpoint 原样核对后，正常原命令只读重试/compact、
abort、旧 binary/CLI 拒绝、v0.17 完整冷恢复与原内容读取全部通过。原 wrapper 始终是
v52，结果明确 `actual_original_wrapper_upgrade=false`。六个实际 Controller incarnation
均保留 PID/原 argv/native wait -15，原预算不变。没有用 fresh 内容替代原数据升级。

`controller-storage-ci-profile-final/result.json` 在 CI 无历史 executable 时使用显式
legacy profile，结果保留
`historical_binary=false`；该配置的 13 次实际 Controller 启动与完整 cold 组合也通过。
它不替代上述真正历史 binary 的证据。

实际构建的 Controller SHA-256：
`27ed4689fad5a9c57b6e937e1367a26ec92bcd501181e93b69a8644210d721c0`；
实际 storage probe SHA-256：
`2ff89c4104c7c29b256368e74363b3c1cda117154bcb24db5a5157b64c8120e2`。
结果文件逐一保存 source/binary identity、命令、原 SQL/文件、原 HTTP 观察与退出码。

7 项实际文件/SQL/继承描述符定向检查覆盖原子格式回滚、最后 clone、旧连接排空、
原 raw rows、空库 fence、read-only observer 和真实非 writer child 继承。全仓 Rust
1021 项通过、2 忽略；Python 全量回归通过、1 跳过。新增 child 配置闭合/policy
回归包含在完整 Rust 结果中。Clippy all targets/features、private Rustdoc、351 文件
strict mypy、Ruff、格式和函数文档已通过；最终分支记录保留对应原日志。

## 保留失败与完成边界

native-01 的故障样本复制没有先完成原 WAL checkpoint，未计为通过。native-02
的同进程 SQLite 观察阶段曾报格式错误；新的实际进程重新检查原文件
`integrity_check=ok`。检查器改为实际退出生产进程、显式关闭观察连接后再迁移。
native-03 暴露 accepted v1 重复 journal transition 对空闲观察连接的干扰，已移除
不必要的代际排空，并增加实际独立 read-only observer 组合；native-04 的旧 CLI
诊断/profile 不匹配保留，旧工具现在
针对完整新 profile 拒绝，当前工具独立验证原 v0.16 profile 不能隐式读取 v1。
这些目录/退出码保留，均不重写为成功。

这关闭 runnable Controller 所选存储合同的本地 writer/lifetime/generation 缺口。
仍须完成 supervisor 原 SQL replacement/旧 child 日志迁移、完整 v1 部署 adoption、
Raft/online producer/mTLS 同成员多数、原内容与路由恢复、GC hot journal 维护及其它
N7 I/O/监听预算。v0.17 不包含新 Raft content roots；其完整恢复另行版本化。
原全部 P1–P7 继续必需，尚未达到 SoftwareComplete。Node/设备/模型/模拟器均为 0，
不建立物理停止、跨主机 RTO/RPO 或连续性证据。
