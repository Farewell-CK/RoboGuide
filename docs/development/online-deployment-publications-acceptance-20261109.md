# 2026-11-09 在线部署发布软件验收

基线 `8a08e1f7`，维护分支 `codex/rg-hot-enrollment-maintenance`。本次实现并软件验收
RG-HOT-002（PhysicalEntityRegistry 在线接线）和 RG-HOT-003（DeploymentFeasibility
受控版本发布），合同见 [ADR-0242](../decisions/0242-online-deployment-publications.md)
及[请求/恢复说明](online-deployment-publications-v0.1.md)。未连接现场主机或机器狗。

## 代码与权威

两个 GET/POST 入口都沿原全局管理员授权、FIFO writer、Control provenance/声明摘要、
原 checkpoint/command journal 原子提交。新闭合 RegistryCommandKind 拒绝旧 writer。
新请求在写入锁内取当前可行性版本；旧 runtime policy/candidate 使用原冻结版本。
现行物理表不由 checkpoint 恢复，冷启动必须显式提供最新外部物理文件；原 feasibility
启动文件仍是原 lineage，在线版本的完整来源另外保留。没有文件监听或静默覆盖部署文件。

普通测试夹具的 ControllerState 初始化增加空 published_deployments 字段，是新应用状态
的构造接线；没有给这些历史测试增加虚构 Node、设备或新权限。

## 已验证

- integration-server 全部 324 项测试通过；Domain/Control/Integration/Orchestration
  库回归共 382 项通过，保留原有 2 项忽略。共 706 项 Rust 测试通过。
- 新增测试验证管理员/租户拒绝、原回执重复、物理防回退与外部恢复来源、原始完整
  可行性文档、短缺/篡改原历史拒绝、错误 registry/version/source digest/物理版本拒绝、
  历史 wrapper 降级拒绝、两条路径 SQL 故障零部分发布，以及真实原 runtime policy
  在新版本发布和冷 reattachment 后仍使用第一版。
  另外验证旧部署不能隐式获得新增 startup profile，以及 SQL 准备后撤销权限会使
  原事务和命令回执全部回滚；最终 Commit 使用原 AuthorizedRequestGate 再检查权限。
- 工作区 all-targets/all-features Clippy `-D warnings` 通过。维护脚本 Ruff、format、
  Linux strict mypy、函数文档检查及 Rust 格式、git diff 检查通过。
- `check_online_deployment_processes.py` 使用当前 Controller、两个真实通用 Node、
  软件 mTLS CA、独立 Linux SQL 与纯只读 fixture，完整通过。

实际进程链先以 entity-existing 和第一版来源启动一个持续占用资源的正常 v0.8 Task。
第二个真实 Node 独立注册后，管理员在线增加 entity-new、发布包含新端点的可行性。
其后正常 Mission 通过匹配/Commit/Bind，仅在 new Node 完成；原任务的 Actor/资源占用
和原 State source epoch 不变，三个进程都未在热阶段重启。之后允许原任务完成，
两个 Attempt 均为 Completed，分配为零。

冷阶段验证三种拒绝/恢复：旧物理文件被 provenance 防回退拒绝，替换原 feasibility
启动来源被 lineage 检查拒绝，真正旧程序读取新 SQL 副本以 unknown variant
deployment_feasibility 拒绝。显式提供当前物理文件、原 feasibility baseline 后，当前
Controller 恢复两条原 Attempt、最新发布及原重复回执，未补写新执行或占用。

额外回归重跑身份热采用的实际 mTLS 进程链，以及姿态目标的 17 项实际软件进程检查。
后者仍区分本机执行完成、Reported Goal、负证据与物理效果，未将现场旧任务改为新语义。

## 证据与边界

公共证据目录为 `C:/workspace/DEAIOS/lab/rg-maintenance-audit-20261109/`。最终
`online-deployment-result.json`、`online-deployment-process-verified.log` 保存最终二进制和
源码 SHA-256、原 session、确切 Node/Attempt 对应和冷热拒绝结果，检查前后摘要不变。
完整私有软件目录为 WSL `/home/aios/.cache/rg-online-deployment-20261109-verified/`，
软件私钥未进入仓库。Rust 完整日志为 online-deployment-server-verified.log、
online-deployment-core-tests.log、online-deployment-clippy-verified.log；同一最终二进制的
身份与姿态过程分别为 online-deployment-identity-verified.log 和 online-deployment-posture-verified.log。

最终 Controller SHA-256 为 `1705ca1ec00957ebfedf9a260293a8e0c942895e5d392af81de9019e593b14d1`；
Node 为 `35a20fc7dd0d0bf66e61cb606d943e62471ad1ec3fc77f6f4dda7070f5061f3d`。
真正先前 Controller（已支持身份采用、尚无本次发布能力）的归档摘要为
`af2051f292999ba9c52a1f970830ced555041e7769293a67691775cd4a511c7c`。

早期失败记录保留：夹具授予 viewer 全局通配被原权限规则拒绝，错误采用 v0.10 而来源
要求 v0.8 session，Task 状态误用 Running 而实际为 Active，以及错误把 published-contracts
预检当成物理 placement 准入。修正夹具后由实际 Commit/Bind/执行与资源证明接入，
没有为通过测试放宽生产合同。身份回归最初选中已支持 adoption 的旧程序，不能用于
“修复前程序拒绝”检查；后用真正修复前的 2d6a043c 归档二进制完成原门禁。

这是单主机隔离软件验收，不代表现场已升级、真实定位/运动成功、完整 MI World
输入或跨主机 HA/长期 SLO 已验收。历史真实任务及 Robonix 本机控制/安全职责保持原样。

## e07237ad 再审后的两项修复

P1 原 Mission 重传：新增的实际进程回归先在原程序上失败，在线发布新 feasibility 后，
原请求返回 409 `mission actor already has a different candidate restriction`。记录保留在
`deployment-replay-red.log`。现在在原 writer 内先核对已存在的 typed plan、Group、
确切租户及存在时的原 HTTP 回执。相同请求确认原接受结果，不进入新来源/verifier/
容量准入，也不写 checkpoint、更新原准入时间或再次 dispatch。不同排版可重传，
同 ID 改目标或 Group 仍拒绝；缺失的原 HTTP 回执或租户不补造。运行期已改为另一
plan/Group 的 Mission 仍按原冲突语义拒绝，不通过旧 POST 复活。

P2 普通查询：移除 HTTP 总入口对 feasibility 的无条件解析。发布阶段验证并建立
不可变 Arc 缓存，Controller clone/查询共享它。缓存不序列化，冷恢复完整检查所有
原文、Control 摘要和启动 lineage 后才一次性安装；后续历史损坏时不保留部分缓存。
持久格式、原证据时钟和旧 writer 拒绝边界保持不变。

最终验证：

- 324 项 integration-server 和 201 项 Control 测试通过，共 525 项；2 项原有 Control
  测试保持忽略。包含重传后完整 checkpoint 不变、不同 JSON 排版、跨租户拒绝、
  不补造旧回执、缓存共享、冷缓存重建及后续历史损坏拒绝。
- workspace all-target/all-feature Clippy `-D warnings`、Rust 格式、Python Ruff/format、
  strict mypy、函数文档及 git diff 检查通过。
- 扩展的 `check_online_deployment_processes.py` 验证发布前后、完成后、冷恢复后
  重传仍为 202；原准入回执/时间、Attempt 和资源保留，相同 ID 换目标为 409，viewer
  重传为 403。仍完成两真实软件 Node 的匹配/Commit/Bind、旧文件拒绝及真正旧 writer
  拒绝。身份热采用进程门禁与姿态目标 17 项进程回归也全部通过。

另用同一私有 Controller 进程、相同 mTLS 主体及端点，在发布前后各测八次请求。
来源为 1,250 条 synthetic 记录，POST 为 965,904 字节。各自中位耗时如下：

| 测量 | health | inventory |
| --- | ---: | ---: |
| 审阅旧版本：发布前 | 7.69 ms | 6.91 ms |
| 审阅旧版本：发布后 | 137.76 ms | 116.20 ms |
| 修复版本：发布前 | 7.01 ms | 7.35 ms |
| 修复版本：发布后 | 8.17 ms | 6.77 ms |

这仅是 debug 构建的本机软件对比，不是生产 SLO 或跨主机性能承诺。新结果保存在
`deployment-repair-readpath-performance.json`，原审阅结果未覆盖。实际进程回执和源码/
二进制摘要见 `deployment-repair-process.log`；身份、姿态回归分别见
`deployment-repair-identity.log`、`deployment-repair-posture.log`。测试及最终 Clippy 为
`deployment-repair-tests-isolated.log` 和 `deployment-repair-clippy-final.log`，均位于上面的
公共证据目录。初次构建遇到其他 checkout 共用 target 的旧缓存，相关失败日志保留；
最终全程使用独立 `/home/aios/.cache/rg-deployment-repair-target-20261109`，三项门禁
及性能测量使用同一最终二进制，未操作其他任务进程或现场部署。

最终 Controller SHA-256：`67bb400e546f7e0f1f10d9af46b0a6e777cc66a180fda275ee127a368990bcf7`；
Node：`9a2a9ce892908f15aec91a03e23cf46a41c3716147e4a0f667db711322043b36`。
本次未连接 ThinkPad/机器狗、未重启现场服务、未下发任何动作，main 未变更。
