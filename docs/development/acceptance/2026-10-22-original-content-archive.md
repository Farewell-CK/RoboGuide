# 原内容 Archive 与 Catalog 恢复验收

唯一开发分支仍为 `codex/nonrelease-remediation-closure`，接续 `bd50aab`；其
[完整 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39749924404)成功。
本包实现 [ADR-0152](../../decisions/0152-original-archive-content-and-catalog-recovery.md)
及 [ADR-0153](../../decisions/0153-artifact-reference-authority-inventory.md)，不关闭
完整 N1，也不把其余 N2–N7 或设备验收宣布完成。

## 实现与 authority

显式 `roboguide.archive-command/v0.2` 将原 ID、实际 principal/tenant、原 Controller
store identity、实际 source owner、部署目录、完整 checkpoint、Catalog、内容和原
kernel budget 先持久到 SQL。实际 adapter 在接收端先发布原 intent/owner/reference，
再逐块复制和检查原预算/当前授权。原 publication 与实际所有 bytes 校验通过后，
一次事务保存完整原 Catalog recovery head/rows、history floor、archive manifest 和
精确原 compact receipt。source pin 此时退休；接收 CAS 的 Archive reference 独立保留。
上传历史、原 Catalog/lineage 和 localization 由原完整 envelope/payload 重放，
不由声明或重建结果代替。prefix/tail 在同一 SQL 锁内读取，不能跨 compact 丢失证据。

原命令重试不复制、不开新 budget。pending 发布的对账只核对并 sync 原已存在的文件；
complete 重试只读。失败保留原 intent，阻止其它 Archive 命令取代它。内容 I/O、实际
transport witness 回调在 EventLog mutex 外执行，原 application writer 全程保留；
返回事务前重查当前授权、write availability、原 floor 和精确 pending command。
避免存储锁下等待读取 Controller 的权限回调。

wrapper v49 的原随机软件 store identity、journal marker 与空 journal 均 mandatory。
Artifact storage v3 沿用原 owner/inode，以原升级 intent、owner/fence/header 和完整
已知 consumer inventory fence 旧 binary/CLI。inventory 有不可缩减的持久 head；
缺已知 consumer 行不能误判为无引用，partial 登记保守保留。清单不授予 Memory 权限。
recovery v0.12/bundle v0.4 要求所有已知原 authority 及 Controller/CAS/Archive writer，
验证原引用与同 owner CAS，并保存 mandatory 嵌套空目录。restore receipt v0.12
保留原预算和不确定结果，恢复本身不启动服务。

## 实际证据

证据根目录为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`：

| 实际 gate | 结果及范围 |
|---|---|
| `archive-zero-final` | 实际 mTLS Controller 上传、SQL compact 故障、原 pending、实际重启/精确对账、冷备份/恢复、原上传观察与独立 bytes；0 Node/设备/模型/模拟器 |
| `archive-file-final-03` | 两个真实软件 Node、原文件发布、两次 CPU hash/独立 verifier、原 import/use/revoke、发布与退休 SQL 回滚、原退休；Archive 后完整原 Catalog 重放和 Controller 重启，精确重试不改文件 |
| `catalog-archive-cold-final-03` | 直接使用已完整退出的上行实际 SQL/CAS/Archive；CLI backup/verify/restore/幂等 retry，独立核对原 Catalog、Archive command、budget 和接收方两份实际 bytes |
| `archive-quota-final` | 实际零 Node quota 配置/重启/旧 wrapper 迁移，当前 wrapper v49，无 Node registry 或设备事实 |

零 Node gate 独立删除接收端一份 CAS，重新计算完整 manifest 后仍拒绝。删除已知
consumer 登记行并重新计算 manifest 也拒绝；遗漏 Archive/consumer 不发布备份。
实际旧 Controller `bd50aab` 的 binary SHA-256 为
`d5f4d1b1e694968e78d0be2a3f7b7ee8ba05a82243035bb1b6d9865880e10000`，
在原 v49 SQL 副本与 fresh legacy CAS 上以实际退出拒绝新 wrapper，排除 CAS 旧路径
fence 混淆。实际旧 CLI 从精确 Git ref `bd50aaba3f389b633f99a4a166cfc061bbe99947`
提取，以其原 v0.11 profile 实际拒绝 v3 owner。

Archive 零 Node/文件门禁的原 Controller binary 为
`6a0d66efc3e95e19eeb46ba555205cc701936a3d33247816f4bf9514d542836b`；
quota 单包构建为 `099e3f8a559be67b8c2cebdc4296e404920c4af837e7a38dc7a7c7814a363f89`。
各原 result 保留实际源码、配置和 binary identity；不同构建结果不合并成一份冻结 binary。
Node binary 为 `7103a3ba52c4ae9829b5f67e214d936cb4826e0a5e515d78249570a33c168c1e`。

中途失败原样保留：native-01 的角色配置不合法；native-02 的 publication 路径错误；
native-03 验证了管理员不能冒充 composition-owned publisher，没有放宽产品入口。
实际 Node 的文件门禁才产生正式 Memory 发布。archive-file-01 暴露 storage/witness
锁顺序风险；file-02 已 compact，但检查器错误地将全部原引用同单 selector 比较；
file-03 重启后尝试使用已失效 Node leaf，被真实生命周期拒绝，未虚构新 Node 权限。
`archive-file-inventory-final` 的 Unix socket 路径为 113 bytes，进程明确报
`AF_UNIX path too long`；改用短目录重跑。失败均不算成功，不刷新原 TTL/budget。

## 工程检查与剩余工作

工作区 Rust 全量 **971 passed，2 ignored**；另有 Catalog 原命令/SQL 回滚/不重复
effect/mandatory authority 及实际 Archive 重放检查。Python 全量 **3057 passed，
0 skipped，488.54 秒**，真实 Python 3.9 bridge 检查不启动 Habitat/模型；inventory
head 修改后恢复工具 **81 passed**。Clippy、私有 Rust 文档、493/494 文件格式/Ruff、
324 源文件 strict mypy、函数文档和 MkDocs strict 检查通过，最终远程 CI 独立运行。
原日志保存在该证据根目录的 `archive-*` 文件；两项 Rust ignored 保留其原含义。

尚须全部完成：未发布 Archive 的原 copier fence/明确 abort，接收归档的显式退休与
tombstone-before-unlink GC，完整 Catalog/lineage/Snapshot/Archive/Raft/online
reference 对账，未归属旧 Catalog 显式迁移，以及原计划全部 N2–N7。当前复制是同主机
软件文件维护；它不证明在线多数派、跨主机恢复、设备停止、物理连续性或 SoftwareComplete。
