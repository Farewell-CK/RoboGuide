# RoboGuide Episode51 最终重复执行与通用语义验证报告

## 结论

最终代码在冻结的 Episode51 / seed40 上连续执行三次，三次均由真实
Interpreter → Planner → Reviewer → Control → Node → 原始 EMOS Stage2 → Habitat 链路完成。
每次只创建一个 Mission Request，不注入历史 MissionPlan，不使用 B2 fallback，也没有重试失败
样本。三次均在第 108 个 simulator step 获得 Habitat 官方 `pddl_success=true`，Mission 状态为
`Completed`，provenance、Formal population 和 benchmark population 判定均有效。

这次成功不是通过让两个执行去同一个目标得到的。三次运行中：

- agent_0 的 committed destination 和真实 `nav_to_obj` 参数都是 `any_targets|0`；
- agent_1 的 committed destination 和真实 `nav_to_obj` 参数都是 `TARGET_any_targets|0`；
- TARGET 谓词在第 26 step 首次成立并保持为 true；
- object 谓词在第 108 step 首次成立，联合 PDDL goal 同步成立。

精确代码身份：

- RoboGuide `codex/ep51-repeatable-navigation@7dfdf3891487af51cd59f97cabc1796800d073d8`
- EMOS `codex/oracle-nav-3d-completion@8d082d3a41af8219ca43719d24c553e9d61771fb`
- RoboGuide 基线 `a24feeba4b84336d870a235e56c46fb91b2d6d67`
- Episode `51`，seed `40`
- dataset SHA-256 `5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca`

机器可读汇总见同目录 `EP51_FINAL_REPEATABILITY_SUMMARY.json`。它包含每轮关键证据的
SHA-256，未修改三个运行目录中的原始文件。

## 已确认的历史失败原因

历史 guard 运行使用了与本次三轮完全相同的两个初始位置，但在 3,000 steps 后官方失败：

- Spot 的目标参数本来就是 `any_targets|0`，不存在 RoboGuide 把参数传成 TARGET 的问题；
- Spot 在第 76 step、仍位于下层 `y=-2.4695` 时被本地 Oracle 导航错误判为 Completed；
- object 所在导航目标属于另一楼层，官方 `robot_at_object_0_success` 最终仍为 `0.0`；
- Fetch 已使 receptacle 谓词成立，但联合目标缺少 object 谓词，所以 `pddl_success=false`。

原始证据：

- `/data/workspace/code/roboguide-ep51-guard-20260922T023700Z/b1-ep51-seed40/evidence/shared-world-summary.json`
- `/data/workspace/code/roboguide-ep51-guard-20260922T023700Z/b1-ep51-seed40/evidence/diagnostics-terminal.json`
- `/data/workspace/code/roboguide-ep51-guard-20260922T023700Z/b1-ep51-seed40/evidence/stage2-actions.jsonl`

根因位于原始 EMOS `OracleNavDiffBaseAction` 的到达判断。多楼层场景里，仅比较平面位置会把
X/Z 接近但高度不同的位置误判为到达。EMOS 修复
`habitat-lab/habitat/tasks/rearrange/actions/habitat_mas_actions.py` 使用完整 3D navmesh target
距离，未改变路径规划、动作选择、目标语义、步数预算或官方 PDDL 判定。确定性单元测试同时
覆盖“同 X/Z、不同楼层必须未到达”和“真实 3D 邻近仍可到达”。

同一初始状态下，修复后的 Spot 没有在原来的第 76 step 提前退出，而是继续沿跨楼层路径运行，
直到第 108 step 使官方 object 谓词成立。这是目前最直接的因果证据。

## RoboGuide 侧的工程防线

RoboGuide 没有通过重写模型动作制造成功。新增的 Stage2 Contract Guard 在 CrabAgent 分发前
读取模型真实工具调用，并将它与 committed canonical invocation 绑定：

- `mobility.move@v1` 仅允许 `nav_to_obj` 指向完全相同的 semantic destination；
- `wait` 和 peer request 使用各自的闭合参数契约；
- pick/place/reset 等未获当前 invocation 授权的物理工具被 typed local contract failure 拒绝；
- Guard 不替换目标、不重试模型、不生成替代动作，证据写入失败也不会放行已拒动作。

每轮的 `stage2-actions.jsonl` 都记录两条 allowed 决策，目标分别正确；
`stage2-action-audit.json` 均为 2 条写入、0 丢失、0 unavailable、0 写失败。

另外，启动脚本将选择的 EMOS worktree 放入 child process 的 `PYTHONPATH`，并写出
`runtime-source-manifest.json`，记录实际 import 源文件路径和 SHA-256。这避免“命令指向新分支，
Conda editable import 却仍加载旧代码”的不可复现情况。

## Prompt 是否只为 Episode51 调整

最初版本确实存在过度放宽风险：它强调“候选参与者不必全部使用”，但没有同等明确地保护
显式身份和数量要求。最终规则改为对称契约：

- 可用性本身不产生强制参与；
- Planning 的分工自由只能在已确认约束内使用；
- 显式身份、全称范围、最小数量和精确数量不得被 Interpreter、Planner、Reviewer 或 Repairer
  删除；
- pairwise distinct eventual bindings 只声明最终绑定彼此不同，不等于提前选择具体
  PhysicalEntityId；environment-authoritative goal 仍执行原有更严格 admission fence。

Prompt 中没有 Episode51、Spot、Fetch、目标名或测试反例。

为了验证泛化性，执行了独立的 MI-only 组件实验：

1. Interpreter 使用三类通用输入，每类重复三次。仅描述三个候选时 3/3 未制造全员要求；
   明确“至少两个”时 3/3 保留最小数量；明确“全部三个”时 3/3 保留全称要求。
2. Planner 使用上述真实 Interpreter 输出，分别生成 1、2、3 个活跃 Actor，说明它既能选择
   候选子集，也不会压缩明确数量要求。
3. Reviewer 初次实验暴露了“distinctness 被误认成具体实体身份”的通用歧义。规则澄清后，
   对冻结的 minimum/universal 计划复核时不再报告该错误；它仍会独立拒绝语义含糊的 inspection
   outcome，因此不是无条件放行。

证据目录：

- `/data/workspace/code/roboguide-participation-semantics-20260922T044602Z`
- `/data/workspace/code/roboguide-participation-plan-review-20260922T045143Z`
- `/data/workspace/code/roboguide-participation-review-recheck-20260922T050137Z`

这些有限样本不能证明所有任务上的模型成功率，也不能替代结构化参与约束的长期评测；它们能
证明本次规则同时覆盖“允许使用候选子集”和“必须保留明确参与数量”两边，而非只优化 Episode51。

## 三轮真实链路对照

| 字段 | trial-1 | trial-2 | trial-3 |
|---|---:|---:|---:|
| MI draft / review / repair | 1 / 1 approved / 0 | 1 / 1 approved / 0 | 1 / 1 approved / 0 |
| Context mode | independent | independent | independent |
| Tasks | object + TARGET | object + TARGET | object + TARGET |
| 每个 Role resource | `space:1` | `space:1` | `space:1` |
| object assignment | node-a | node-a | node-a |
| TARGET assignment | node-b | node-b | node-b |
| Stage2 object tool arg | `any_targets|0` | `any_targets|0` | `any_targets|0` |
| Stage2 TARGET tool arg | `TARGET_any_targets|0` | `TARGET_any_targets|0` | `TARGET_any_targets|0` |
| TARGET 首次 true | step 26 | step 26 | step 26 |
| object 首次 true | step 108 | step 108 | step 108 |
| simulator steps | 108 | 108 | 108 |
| official PDDL | true | true | true |
| Mission | Completed | Completed | Completed |
| provenance / Formal / benchmark population | true / true / true | true / true / true | true / true / true |

三轮初始位置、最终位置和 simulator step 数逐字段相同。每轮 physical diagnostics 接受并写入
108 条 step 记录，0 丢样、0 写失败。总采集耗时分别约 0.090、0.096、0.097 秒，单次采集
最大约 0.0028、0.0030、0.0035 秒；动作轨迹也都是 108 条、0 丢失。

运行根目录：

`/data/workspace/code/roboguide-ep51-final-repeat-20260922T050410Z`

每个 `trial-N/` 内包含 Mission Request record、最终 MissionPlan、Controller events、execution
attempts、assignment arrivals、Stage2 action evidence、逐步 physical diagnostics、官方终态指标、
B1 provenance 和 verdict。

## 模型身份限制

代码配置请求 alias 为 `gpt-5.6-luna`。2026-09-20 的归档 Provider 响应也报告该名字；但本轮
相邻的 17 次 MI-only Interpreter/Planner/Reviewer 响应全部报告 `model=gpt-6-luna`。因此当前
Provider 已发生 alias 路由或服务端模型映射变化。

三个完整 B1 运行的 Mission Service 没有归档原始 Provider envelope，不能从这三份运行证据中
独立确认响应模型字段。报告只把三轮称为“当前部署 Provider 路径”的结果，不把它们伪称为已
证明使用不可变的 gpt-5.6 后端。正式 E1 配对前应将 response model identity 纳入 MI 请求证据，
或由 Provider 给出稳定、可审计的版本标识。

## 质量门禁

RoboGuide：

- `uv run pytest -q`：880 tests，全部通过；
- Ruff format/check：通过；
- strict mypy：79 source files，无问题；
- Python function-doc check：通过；
- `git diff --check`：通过；
- `cargo fmt --all -- --check`：通过；
- `cargo test --workspace --all-targets`：通过；
- `cargo clippy --workspace --all-targets -- -D warnings`：通过。

EMOS：

- `habitat-lab/test/test_habitat_mas_actions.py`：2 tests，通过；
- `git diff --check`：通过。

## 尚存限制与下一步

本次已经消除 Episode51 已确认的物理阻塞，并以最终代码获得三次一致官方成功。它不证明其他
多楼层场景、其他目标类型或所有模型输出都成功。下一步应先独立审查并合并两个代码分支，随后
把同一套 source manifest、Stage2 contract evidence 和 physical diagnostics 接入预注册的小规模
E1 对照，而不是继续针对 Episode51 增加 Prompt 条款。

在正式对照前还需解决 Provider response identity 的归档与冻结；这是实验可重复性问题，不是
本次 Episode51 PDDL 成功的后验解释。
