# 2026-10-23 原内容主库显式接管验收

唯一分支 `codex/nonrelease-remediation-closure`，前置 `652ce0a`。
[ADR-0166](../../decisions/0166-explicit-original-content-parent-adoption.md)与
[实际管理入口](../replica-content-parent-adoption-v0.1.md)先于实施固定新旧责任。
证据保留在 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

## 实际产品行为

`controller-replica --content-maintenance` 使用实际原 root/parent writers 和 native
DELETE/exclusive drain。原 child spawn 未取得真实 wait 时拒绝；不从 PID 消失补造 wait。
同事务原 first-intent 目录/pending marker 先于 producer pins、hash 和 publication。
接管后的 parent v6 强制校验原 history/Commit/真实 root producer，而原业务行、原
root identity、legacy raw pins 和缺失历史不变。正常 v5 维持原合同；没有自动升级。

新的保留回执只属于本次接管。预算在 CLI 取得 root/parent writers 前采样，并原样进入
每个新 producer。原来没有的 append/build/install 进程或预算仍 unavailable。同 ID
只读重试；未 Commit v6 时可显式新 repair，保留旧 Unknown。已 Commit v6 但回执丢失
仍为原 Unknown，新命令拒绝重做。原 Root2/parent v4、pending/v6 的丢失日志与旧二进制
均有拒绝检查，没有重建空历史或放宽原引用/时间断言。

## 原始证据与故障

正式 `parent-adoption-archive-verified/result.json` 及其
`actual-original-parent-adoption/result.json`、`actual-original-producer-journal/result.json`、
`actual-snapshot-producers/result.json`、`complete-replica-state/result.json` 均通过。
其中 parent gate 为 **49 个实际 process 步骤**：13 个独立 storage 进程、36 个产品 CLI
进程，全部保留原 PID 和 native wait。正式源使用当前显式 Root2/v4，完整覆盖原 Log、
View 与 Snapshot。独立 `parent-adoption-native-03/result.json` 另以冻结的实际旧探针
产生原 v4/Root2，同时由旧 v5 writer 检查新 v6，49 个步骤通过。

| 原始触发 | 独立检查 |
| --- | --- |
| 原 live root writer、SQL hardlink holder、未关闭旧 SQL 连接 | 指定原因拒绝，未生成新接管意图 |
| 完整原输入 | 原业务行逐项相同、旧 raw pins 逐项相同、实际原 owner/bytes 不变 |
| 原 CAS 文件移走 | 新意图与 pins 留存，原 outcome Unknown；恢复文件后同 ID 仍只读 |
| 原意图后 SIGKILL | 实际 wait -9；新 repair 保留旧原 PID/预算/Unknown |
| 原意图后 SIGSTOP，跨过原 5000 ms，再 SIGCONT | 原 deadline 拒绝效果；没有更新预算 |
| 原 parent SQL 写锁阻断 publication | 原 SQL 回滚、旧业务/pins 不变，独立 repair 后正常打开 |
| v6 已 Commit、root publication 被真实 SQL 写锁阻断时 SIGKILL | 原 wait -9、原结果 Unknown；完整新 heads/bytes 可验证，禁止新命令重复接管 |
| 正常 reopen 与新 Snapshot build | 走原产品存储路径；独立 reader/producer 维持原合同 |
| 删除原 adoption Commit/history | mandatory 原记录拒绝，不解释为空；检查器恢复自己注入前的原 raw 行后继续 |
| 实际旧 v5 binary 读取新 v6 | 原格式门禁拒绝，未取得新权限 |

冻结产品 `controller-replica-adoption-adr0166` 的 SHA-256 为
`42a6e179006936bc8982be1820c1c6724332520fb0b907a083bd132f0f1c45f3`；
冻结 `content-snapshot-probe-adr0166` 为
`6c6a79d41f34d679f52b86b40710121b39c3abc8f0e6fc7b89bc0876020ee5c0`。
旧 `content-snapshot-probe-adr0165` 为
`a4fbcbd6245c9c5df20ada1360aaf80e054df0482f8ddf4fa7abaa729353e0d1`。
实际上传源 Controller 为已冻结的 `3fbb973e…`，完整命令、source identity 与二进制摘要
在原 evidence 中。没有旧 binary 的 CI 仅声明当前原 profile，保持旧 binary unavailable。

首轮 `parent-adoption-archive-01` 在只读观察碰到实际 SQL exclusive 窗口时失败；未将
BUSY 解释为缺意图，后续改为在原观察期限内重查。只读轮询也避开原 WAL→DELETE 排空，
仅查询接管 journal。`parent-adoption-native-02` 的 300 ms 用例在 admission 前已到期，
没有满足预期 fault window，保留为失败。正式 expiry 用例先独立读到原 durable intent，
再暂停原 PID 跨过其原期限，验证拒绝。产品 deadline、native writer/drain 和成功断言
未放宽；这两项早先记录均不计通过。

## 验证与保留边界

7 项新增实际 SQL/CAS 回归通过；完整 workspace Rust **1041 passed、2 ignored**。
全 workspace Clippy/all-targets/all-features、私有 Rustdoc、fmt 通过；维护质量目录
Ruff/format/函数文档、组合 strict mypy **357 files**、网站 9 tests 与 strict MkDocs
中文/英文构建通过。CI 已增加实际产品 CLI build
和显式 parent gate。先前 transport 观察修正 `652ce0a` 的
[完整 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39905083786)与 Documentation
均成功；不将它当作本次新提交的远端结果。

所有本次检查为实际软件进程/SQL/CAS，**0 Nodes、0 devices、0 external models、0
simulators**。原 Archive 的 cold gate 继续证明其原显式静止恢复 profile；它没有将
Root3/parent v6 纳入 recovery v0.17。legacy pin 退休/全局 consumer 覆盖、旧生产 storage
v3 的完整迁移、生产 v0.4、same-voter metadata+bytes majority、在线恢复与路由，以及
完整 N1–N7 仍须完成。此包不宣告 SoftwareComplete 或物理连续性。
