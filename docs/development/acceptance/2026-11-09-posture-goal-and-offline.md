# 2026-11-09 姿态目标与离线状态：无动作验收

本轮完成隔离软件验证和读取器修正。没有连接两只机器狗、创建现场 Pilot 任务、
修改现场服务或原任务记录。底盘充电期间，只使用保留的现场证据与私有软件进程。
本轮不包含新一次物理姿态、导航、双狗协同或 MI 模型实验。

## 已确认的问题与修正

历史任务 `dog2-down-after-mi-stand-20261109T031448Z` 使用 execution-report。
Pilot 的停止并报告分支执行结束，因此 RG 原始状态是 Completed；同一份验收证据中，
后续姿态仍为 standing，用户确认仍站立、只原地踏步。这一历史状态保留。
在原 Controller 观测时刻回放 State，修正后的读取器仍得到 standing，不能判为趴下达成。
回放不代表读取了狗2当前状态。

新评估器复用 [ADR-0033](../../decisions/0033-task-satisfaction-boundary.md) 与
[ADR-0104](../../decisions/0104-state-consumers-belief-and-semantic-verifier.md) 的正式通路，
采用显式 verifier-evidence，而非把“本地调用完成”当作姿态目标完成。
见 [ADR-0241](../../decisions/0241-reported-posture-goal-profile.md)。

读取器另修正了一个真实缺口：原先只限制 RG 五秒上限，没有在读取时同时限制原生
`stale_ms`。现在采用原生有效期、RG 上限、Controller State TTL 中最短的剩余期限；
缺失/非法原生期限拒绝读取，重复读取不会更新原始预算。

评估器要求先在 exact current attempt Completed、Task AwaitingSatisfaction 后记录
anchor，再观察来源、epoch、provider registration 一致且原生采样接收标识、RG sequence、
Controller 接收顺序均更新的样本。缓存重新导出不能满足目标。来源重启后先重新核对
原 attempt 与等待状态、取得新 epoch 的 anchor，不重跑原动作。

## 实际软件进程结果

使用生产 Controller、Node 二进制及正常 HTTP/Node Protocol/Commit/Bind/Task/Verifier API，
配合明确标为 synthetic 的 loopback provider。私有 Controller 和 Node 都进行了冷重启。
所有所谓姿态值都是测试夹具，provider 只完成无物理效果的软件操作。

| 实验 | 实际结果 |
| --- | --- |
| execution-report 基线，调用完成但报告 standing | Mission Completed；复现旧结算条件的局限 |
| verifier-evidence，本机 attempt Completed | Task AwaitingSatisfaction，保留普通资源占用，依赖不启动 |
| 更新的报告仍为 standing，目标为 down | Task SatisfactionRejected、Mission Failed；attempt 仍是 Completed；依赖不执行 |
| unknown/provisional 与重复回执 | 不满足目标、不释放等待屏障，重复请求返回原回执 |
| 原生样本不变、RG 继续导出新 sequence | 评估 unknown，不能用缓存达成目标 |
| Node 在线、外层 State 未过期，原生样本已过期 | 读取 unavailable；Mission 仍等待 |
| Node 离线，内层仍保留 availability=current | 根据库存 liveness 拒绝读取，不能当作实时姿态 |
| Controller、Node 冷重启 | 原 Task 仍等待，不重复本机 Execute；旧 epoch anchor 拒绝使用 |
| 错误 plan digest、错误 attempt | 正常 verifier API 均返回 409 |
| 新 epoch 重新记录 anchor，更新报告为 down | verifier 正常满足目标，依赖恰好执行一次，Mission Completed |
| 正证据重复提交、最终资源与历史 | 返回原接收时间/原回执；普通 Control 释放到零；历史生命周期不改写 |

共 17 项进程检查通过。全程恰好 4 个唯一软件 Execute：基线一个、负例一个、
正例姿态任务一个、正例后继一个；两次重启未增加任何 Execute。
4 个 Runtime attempt 最后均为 Completed；这与负例 Task SatisfactionRejected 不矛盾。

Node 重新可达与新 epoch State 到达之间有先后顺序；恢复测试显式等待新来源 epoch，
没有把“库存重新可达”当作新姿态证据。

## 检查与证据

- 集成测试：85 passed、54 subtests passed。
- `robonix_task` 包 strict mypy：12 个源文件通过。
- 本轮 4 个 Python 文件的 Ruff、format、函数文档检查通过；git diff --check 通过。
- 维护入口：`integrations/robonix-task/tests/posture_goal_process_gate.py`，要求显式二进制和新私有目录。
- Windows 证据根：`C:/workspace/DEAIOS/lab/rg-posture-goal-offline-20261109/`。
- `final/result.json` 保存 17 项结果、4 个 Execute ID 以及二进制 SHA256。
- `final/http.jsonl` 保存原始请求、响应与 verifier 回执；独立 JSON 保存计划、State、资源和恢复状态。
- `retained-field-replay.json` 保存原现场文件摘要、历史 Mission 与原观测时刻的姿态回放。

使用的 Controller SHA256：`6e47fe77c733a944c2acfabc76270aa33d41d70dff4d274eb4877f75629ea917`。
Node SHA256：`2d4bebbdd8f36cadd37358036e6b585a3319590e2d5f2fbf0fffe57aa0b10bcd`。
复用已有缓存二进制，本轮没有改 Rust Controller/Node 核心。

早期测试夹具调试的失败不计为通过：Windows 挂载盘 SQLite 锁冲突、端口碰撞、
来源/channel 配置错误、HTTP 202 与 Task 数组排序假设、恢复时过早读取旧 epoch。
最终进程数据保存在 Linux ext4，进程关闭后复制完整证据到 Windows；夹具按 ID 取 Task，
并显式等待新 epoch。原调试输出保留在原位置。

## 边界与下一步

当前交付是纯评估器和已跑通的私有部署范例；现场尚未登记姿态 verifier source，
没有将现场任务改为自动目标验证。历史 execution-report Mission 不能事后换验收依据。
后续新任务需要在接纳前明确 predicate、完成观察窗口、可信 producer 和发布预算，
经认证 verifier API 绑定原计划与完整 current attempt，不能仅凭评估 JSON 改任务状态。

Robonix 的个别姿态测量时间、遥控器连接和阻尼状态仍未提供；聚合报告不能证明实物效果
或安全停止。本评估器不会下发、取消或重试动作。生产 publisher 仍须扣除其排队和发布耗时、
保留原证据 ID/回执，不能反复生成新 ID 为同一旧样本续期。
运动任务继续遵守原 stop/revoke/Unknown 流程，不能照搬夹具的 noncontinuous 配置。
真机验证须在底盘恢复后另行按新任务执行，本轮的软件通过不替代它。
