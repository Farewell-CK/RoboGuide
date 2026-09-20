# Episode51 / seed40 物理执行诊断报告

日期：2026-09-20。诊断开发分支：`zcode/e1-ep51-physical-diagnostics@6bb098d`
（基于 main `6396137`）。运行目录：
`/data/workspace/code/roboguide-ep51-diag-20260920/b1-diag-ep51-seed40`。
本次运行为故障研究，不计入正式 E1 Pilot 或组织效果统计。

## 0. 结论总览

**唯一一次真实诊断运行未触达物理执行层。** MI 在计划语义边界失败
（`contexts[0] mode concurrent-cooperation requires a Group shared view`），
episode 从未启动，诊断观测文件因此没有产生。按预先固定的规则
（"只执行一次，不因失败、模型输出变化或初始状态不同而反复重跑"），
本次运行终止并归档，未重跑。任务书中的物理问题 A–E 本轮**均无法回答**；
诊断能力本身已完成实现与离线验证，等待下一次真实运行触达物理层。

## 1. 已由本次真实执行直接证明的事实

1. **失败边界与语义**：2026-09-20T04:10Z（UTC），Mission Service 收到
   POST 后由 MI 生成了一份 MissionPlan 草案，其 `contexts[0].mode` 为
   `concurrent-cooperation`，但未声明该 mode 所要求的 Group shared
   view，被 MissionPlan v0.8 校验拒绝；request lifecycle=Failed。
   [b1-request-record.json：`issues`；mission-service.log：12:10:03]
2. **失败发生在 MI 计划校验，先于 Controller 提交**：
   `b1-timing.txt` 有 request_id，无 accepted 记录；Controller 事件
   归档状态为 `not_required`（pre-submission boundary），EXIT=0。
   [b1-verdict.json：`context.event_archive.state`]
3. **归档与准入协议行为正确**：provenance_valid=true、
   valid_for_formal_population=true、system_outcome=FAILURE——一次合法的
   MI 语义失败被如实记录为系统观察，没有被静默丢弃，也没有被错误
   归因为 Control/Node/Stage2。桥健康/语义证据正常发布（ONLINE）。
   [b1-verdict.json：`admission`；shared-bridge.log]
4. **环境预检一致**：emos.env 的 `EMOS_LLM_MODEL` 与正式 EMOS arm 的
   spec 值逐字一致（gpt-5.6-luna，核对通过）；OPENAI_BASE_URL/KEY
   由同一 emos.env 注入；无 401 类认证故障（MI 完成了完整的
   Interpreter→Planner 调用并返回了结构化计划草案）。
5. **无覆盖历史证据**：历史运行目录与 `control-events-complete.json`
   未被触碰；本次运行位于全新目录。

## 2. 新发现（本轮实验的直接产出）

**F'. MI 输出结构非确定性是 B1 可达性的真实故障维度。** 同一冻结
b1-input、同一配置（config/mission.toml + mission-service-b1.toml）、
同一代码基线（MI 代码自历史成功预检的 edc773c 以来无变化），历史
预检（2026-09-20 01:0x）生成了合法双任务计划并被 Control 接受；本次
（04:10Z）生成的草案因 context mode 与 Group shared view 的配对违反
校验而被拒。两次运行的可观察差异点是模型输出本身（Interpreter/
Planner 的非确定性），而非部署、密钥或代码。

证据缺口：被拒的计划草案未持久化（record.plan=null，
draft_revision=0，observations 无 review 证据）——失败原因只有
issues 字符串，无法事后审计模型当时生成的完整计划结构。

## 3. 源码与配置支持的推断

- 校验规则位于 MissionPlan v0.8 语义（context mode 与 shared view
  配对），属于 RoboGuide 计划边界而非模型服务边界：mission 源码中
  该错误消息的抛出点在计划接受路径上。
- Planner 的 provider schema 允许模型选择 `mode` 字段而未强制其与
  shared view 声明配对，也没有 review/repair 轮次能拦截该结构错误
  （本次直接 Failed，未进入 repair）。

## 4. 仍然无法区分的假设

- 历史运行的 Fetch step-1000 停止导航的全部候选解释（技能预算、
  完成传感器、模型误判）**依然全部悬置**——本轮没有产生任何物理
  轨迹证据。
- `concurrent-cooperation` 缺 shared view 是"模型偶发结构错误"还是
  "prompt/schema 对该 mode 的引导不足导致的高概率失败"，需要多次
  采样才能区分；按规则本轮不做重复运行。

## 5. 诊断能力交付状态（已实现、离线验证、未触达）

- `habitat_local_eaios/diagnostics.py`：`ROBOGUIDE_B1_PHYSICAL_DIAGNOSTICS=1`
  开关（默认关）；初始世界状态（真实位姿/朝向、目标实体位置、官方
  逐谓词 `Predicate.is_true` 真值、联合 pddl_success、消费的 seed）、
  每步 JSONL 流式记录（技能、动作摘要、位姿、cur_skill_step/
  max_skill_steps/force_end_on_timeout/high-level 调用标志、原始
  oracle skill_done、逐谓词真值、双通道联合真值、done/episode_over，
  含 settle 步）、终态真实世界状态（独立于早期本地完成快照）。
- 只读保证：全部通过既有只读属性读取，不改变 act/step/技能切换/
  终态判断调用顺序，不调用任何改变模拟器状态或 RNG 的 API；缺失
  字段降级为显式 unavailable；行级故障计数不中断执行；流式无界
  内存风险。
- 离线验证 8 项全部通过（test_diagnostics.py）；全仓库 711 passed、
  ruff/mypy-strict/docstring 门禁全绿。

## 6. 下一步修复建议（按优先级，均附证据）

1. **MI 计划结构可靠性**（本轮新发现，阻塞一切 B1 物理诊断）：
   Planner provider schema 或 prompt 应把 `mode` 与 Group shared
   view 声明配对约束（或由 review/repair 拦截该结构错误重试）。
   证据：本次 issues + 与历史成功运行的配置/代码一致性。
2. **被拒草案持久化**：计划校验失败时把完整草案存入 request
   record（或 observations），使结构错误可事后审计。证据：本次
   record.plan=null 且无 review 证据。
3. 修复后重跑物理诊断实验（仍按单次规则），回答任务书 A–E。
