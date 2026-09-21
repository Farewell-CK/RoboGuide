# Episode51 / seed40 完整链路诊断报告

日期：2026-09-21。实验代码：`cb3eee10d1aae6fd33a714de4ccfd4e5770f6f38`。本次只执行预注册的一次完整链路尝试，只创建一个 Mission Request；没有注入历史 MissionPlan，没有修改 Episode、seed、官方目标、初始状态、3000-step 上限或 benchmark 判定，也没有因失败追加请求或重跑。

原始运行目录：`/data/workspace/code/roboguide-ep51-full-chain-diag-20260921T015004Z/b1-diag-ep51-seed40`。本报告分支只保存必要的脱敏证据和摘要；`RAW_RUN_SHA256SUMS` 可核对服务器上的完整原始目录。

## 1. 结论

本轮**没有进入物理执行**。真实链路到达了 Interpreter、Planner 和 Reviewer；MI 生成两个目标完整、资源正确且 Context 为 `independent` 的计划，Reviewer 返回 `approved=true`。但是该计划还声明：

```json
{"kind":"distinct-physical-entities","context_roles":["robot_a","robot_b"]}
```

当前 B1 启动脚本未向 Controller 安装 deployment-owned physical-entity registry。Control 在 matching 阶段发现有 physical distinctness constraint、但 constrained candidate Nodes 无法映射到 Physical Entities，按契约 fail closed，Controller POST 返回 HTTP 409：

```text
control rejected orchestration: invalid proposal: physical entity registry does not cover every constrained candidate Node
```

这是本次最早、也是直接导致终止的故障。Controller 没有创建 Mission、Group、TaskExecution 或 assignment；Node、原始 EMOS Stage2 和 Habitat 没有收到执行。因此本轮没有 Spot/Fetch 位姿、导航轨迹、skill budget、finished sensor、逐谓词真值或官方 `pddl_success`。不能把 `BENCHMARK_UNAVAILABLE` 写成 `pddl_success=false`。

协议归档正确区分了 SUT failure 与 benchmark outcome：`provenance_valid=true`、`valid_for_formal_population=true`、`system_outcome=FAILURE`、`failure_owner=SUT_SYSTEM`、`valid_for_benchmark_population=false`、`benchmark_outcome=BENCHMARK_UNAVAILABLE`。[证据：`evidence/b1-verdict.json`]

## 2. 代码修复、审查与 main 合并

### 2.1 异常退出证据闭合

本次执行前完成并合并了两个诊断提交：

- `db114d610fe89076ba4c3166752df3b01d85421f`：将 per-step diagnostics 和 action trace 改为有界批量写入，终态 flush 最后一批，记录采集时间、丢弃数和写入失败；默认关闭，启用时不增加 `actor.act()` 或 `gym_env.step()` 调用。
- `cb3eee10d1aae6fd33a714de4ccfd4e5770f6f38`：把 `_pair_loop` 的 setup、`actor.act()`、`gym_env.step()`、post-step 和 settle 阶段全部纳入 `try/except/finally`；在任何退出路径先 flush action trace，再 best-effort 写 terminal diagnostics。终止原因包含具体阶段和原始异常类型，例如 `execution_exception:gym_env_step:RuntimeError`。

`_record_terminal_diagnostics()` 隔离诊断读取、序列化和文件写入异常，不吞掉也不替换原始物理异常。post-reset、pre-loop setup 失败也会尝试记录终态。另修复一个在全量测试暴露的单 episode 竞态：coordinator 在开始 pair execution 前原子标记 episode 已消费，避免 terminal publish 前短窗口误接收第三次 invocation。[源码：`integrations/habitat-local-eaios/habitat_local_eaios/shared_world.py:75,160,403,1000`；测试：`integrations/habitat-local-eaios/tests/test_shared_world.py:241,253,264`]

### 2.2 行为边界审查

诊断仍由显式 `ROBOGUIDE_B1_PHYSICAL_DIAGNOSTICS=1` 开启。关闭时走 disabled recorder；开启时只读现有 environment、policy 和 sensor 状态。修复没有改变 MissionPlan、MI、Control、Stage2 动作选择、导航技能完成条件、Gym step 次数、PDDL goal 或官方 `pddl_success` authority。异常路径仍向上抛出原始错误，Node/Control 所见终态不会被诊断 I/O 改写。

### 2.3 合并和质量门禁

代码分支 `codex/ep51-full-chain-readiness` 已推送并 fast-forward 合并 main。执行实验时本地 main 与 `origin/main` 均为 `cb3eee10d1aae6fd33a714de4ccfd4e5770f6f38`。

已实际通过：

- targeted Habitat diagnostics/shared-world/CrabAgent：40 tests passed；
- full Python `uv run pytest -q`；
- Ruff format/check；
- strict mypy（Mission/integrations 与 evaluation scopes）；
- Python function-doc check；
- Cargo workspace tests；
- `cargo fmt --check`；
- workspace all-targets Clippy with warnings denied；
- `git diff --check`。

本轮没有进入 `_pair_loop`，所以异常闭合修复只有确定性测试证据，尚无本次 live physical run 证据。不能把“代码已部署”写成“本次物理诊断已经实测完整”。

## 3. 部署预检

预检在 detached、干净代码视图 `/tmp/roboguide-ep51-run-cb3eee1` 中完成，运行目录全新创建。冻结 workload 为：

| 字段 | 值 |
| --- | --- |
| Episode | `51` |
| Habitat seed | `40` |
| Scene | `data/scene_datasets/mp3d/pRbA3pwrgk9/pRbA3pwrgk9.glb` |
| Dataset revision | `mobility_episodes_1` |
| Dataset SHA-256 | `5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca` |
| Frozen input SHA-256 | `1026761241f906ca2a35ab567082ab3d281d76524d5c8ab38a2c8522bbaf87c6` |
| Max simulator steps | `3000` |
| Diagnostics | enabled, schema `roboguide.e1.physical-diagnostics/v0.2` |

External EMOS checkout 为 `e9501db45d634b087bf5d1a14228266685e8feeb`，tracked-diff digest 为 `172268dd4cf44e8e50ba1d51de2f9f7a4ff5377b32d7aa96bfa21f1b77c0ee78`，model client digest 为 `a63f6af9e98731d80ae578be59d98dc317b7614de36fc8f8de5ab538e1d22b74`，与 readiness manifest 冻结值一致。

Habitat Conda import、CUDA（GPU 1）、磁盘、构建产物及端口 8070/25060/28060/28090/28100/28102 均通过。MI 和 Stage2 的 credential 只检查为 present；证据包没有保存 key、Authorization header、敏感环境值、key 前后缀或摘要。[证据：`evidence/deployment-preflight.json`]

## 4. 唯一 Mission Request 的时间线

| UTC 时间 | 事实 | 证据 |
| --- | --- | --- |
| 01:52:45 | Runner 开始唯一一次 submit-and-wait | `evidence/b1-timing.txt` |
| 01:52:46.034 | 创建 request `request-07f444f230c3465ba9bc852b543c0cb7` | `evidence/b1-request-record.json#/created_at_ms` |
| 01:54:54.429 | revision 1 draft 已持久化，生命周期 `Reviewing` | 原始 `mission-service.sqlite3` 历史页；最终 record 的 revision/digest 与其一致 |
| 01:57:15.523 | Reviewer 批准 revision 1，无 issue | `evidence/review-result.json` |
| 01:57:15.628 | Mission Service 向 Controller 提交最终计划 | `evidence/b1-request-observations.json#/submission_evidence` |
| 01:57:15.686 | Controller HTTP 409；request 进入 `Blocked` | 同上 `#/failure_evidence` |
| 01:57:16 | Runner 归档终态，未再次 POST | `evidence/mi-wait-outcome.json`、`mission-service.log` 原始文件 |

Mission store 中 `mission_requests` 行数为 1。Mission Service log 只有一次 `POST /v1/mission-requests`；之后只是对同一个 request_id 的 GET。`repair_attempts=0`，没有 Planner regenerate，也没有 Repairer 调用。Stage2 provider 调用次数为 0。

## 5. MI 结果

### 5.1 做对的部分

最终计划保留两个官方目标对应的两个 Task：

- `reach_any_targets` → destination `any_targets|0`；
- `reach_target_any_targets` → destination `TARGET_any_targets|0`。

两个 Task 均 `depends_on=[]`，同属 `shared_scene`，Context `coupling_mode=independent`、`relations=[]`、无 shared view。每个 Role 都带 `resources=[{kind: space, units: 1}]`。这证明新版 Planner 在此次真实输入下没有重现此前错误的 `concurrent-cooperation`，也没有丢失 authoritative semantic goal。[证据：`evidence/final-mission-plan.json`]

### 5.2 未通过语义审查的部分

计划同时给两个 ContextRole 添加 `distinct-physical-entities`。权威 goal 仅为：

```text
any_at(any_targets|0) AND any_at(TARGET_any_targets|0)
```

它不要求两个不同 Physical Entity。Grounding Context 只有 `agent_ids=[0,1]` 和实体目录，没有可绑定的 physical-entity references。冻结自然语言提到 Spot、Fetch 并要求系统自行分工；但“有两个可用机器人/有两个 Actor/有两个谓词”不能自动推出硬性物理互异约束。当前 Planner Prompt 也明确要求：除非 semantic goal 需要不同 executor，否则 `executor_constraints=[]`；不得仅因两个 Actors/Roles 添加 distinctness。[源码：`mission/prompts/v0/planner.md:102-109`]

Reviewer Prompt 同样要求没有 semantic reason 时不得要求 distinctness。[源码：`mission/prompts/v0/reviewer.md:116-120`] 然而本次真实 Reviewer 返回 `approved=true, issues=[]`。因此：

- **已确认**：真实模型此次没有稳定遵循现有 executor-constraint 指导；
- **已确认**：当前确定性 plan validation 只检查该 constraint 的结构和引用合法性，不证明其语义依据；
- **未证明**：Prompt 文案本身必须再次扩写；该规则已经存在，单样本更直接暴露的是模型服从性与缺少确定性语义 admission；
- **不能采取的绕过**：删除 Control 的 registry check、把两个 `any_at` 硬编码成 distinct、或为通过实验事后修改计划。

问题 1 的回答是：MI 生成了结构合法、目标覆盖完整、资源正确的计划，但最终 Review 未发现一个会使部署无法执行且缺乏 authoritative-goal 依据的 physical distinctness constraint；所以不能称为“语义正确且可执行的 MissionPlan 已完整通过”。

## 6. Control、Node、Stage2、Habitat

Control 的行为符合当前 authority boundary。`core/control/src/matching.rs:333-365` 在 Mission binding semantics 要求 physical entities 时，从 deployment registry 构造 Node→PhysicalEntity 映射；distinct actor groups 非空且任一 candidate Node 没有映射便返回本次 `InvalidProposal`。B1 脚本启动 integration-server 时只传入前五个必需参数，没有传 optional actor placement 和 physical-entity registry 文件；integration-server 只会在显式提供第七个参数时安装 registry。[源码：`scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh:204-205`；`apps/integration-server/src/main.rs:300-334,428-437`]

结果如下：

| 层 | 本次实际结果 |
| --- | --- |
| MI | one request；Planner revision 1；Reviewer approved；final lifecycle Blocked |
| Control | HTTP 409 before Mission creation；无 mission/group/task registration |
| Matching/Scheduling | 未产生可提交 proposal；无 node/slot/resource assignment |
| Node | 两节点已成功注册，但未收到 Execute；execution attempts 为空 |
| Shared-world adapter | 已完成初始化并发布 authoritative semantics；未收到 assignment pair |
| Stage2 | 未调用模型、未生成 AgentArguments、未选择 skill |
| Habitat physical episode | reset/physical execution 未开始；0 execution steps |
| Benchmark | authority document absent；官方 PDDL outcome unavailable |

Controller SQLite 含 112 条启动和 Node 注册事件，但没有本 mission_id 的事件。归档器将 event archive 标记为 `not_required / pre_submission_boundary`，没有把这些部署事件冒充 Mission execution evidence。[证据：`evidence/controller-event-archive.json`、`evidence/execution-attempts.json`]

## 7. 本轮七个物理问题的答案

1. **新版 MI 是否生成并审查通过语义正确计划？** 生成并批准了一份 `independent`、双目标、双 `space:1` 计划；但额外 physical distinctness 缺少 authoritative-goal 依据且 Reviewer 未指出，最终不可执行。结论为部分改善、未完成闭环。
2. **Control 如何分配两个任务和资源？** 没有分配。matching 在任何 assignment 前因 registry coverage 缺失拒绝。
3. **Spot 到达后是否保持在目标？** UNKNOWN；Spot 没有执行。
4. **Fetch 轨迹及为何约 1000 steps 后退出导航？** 本轮不可用；Fetch 没有执行，不能用历史轨迹填补本轮事实。
5. **Fetch 退出是否与 skill budget、finished sensor 或高层切换有关？** 本轮未观测。历史运行仅有强代码/轨迹支持的 1000-step budget 假设，仍缺少直接 runtime flags；本次没有新增证据。
6. **两个 PDDL 谓词何时成立/失效、联合目标为何成功或失败？** 本轮无物理 episode，两个逐谓词和联合 `pddl_success` 都不可用。系统失败发生在 Controller admission。
7. **诊断是否完整、开销和丢样如何？** 未产生 physical diagnostic stream，无法进行 live 完整性或开销评估。代码具备计数与终态 flush 能力，但本次不能给出 capture seconds、max latency 或 dropped records。

## 8. 与历史 Episode51 物理失败的比较

历史运行 `/data/workspace/code/roboguide-stage2-provider-_hyz84nd/b1-preflight-episode51-seed40` 的 MI 计划同样是 `independent`，但 `executor_constraints=[]`。Controller HTTP 202 后分别分配 node-a/slot-a 和 node-b/slot-b，进入原始 Stage2 与 Habitat；Spot 在 step 90 得到本地完成，Fetch `nav_to_obj` 到 step 1000 后转 wait，episode 在 step 3000 以官方 `pddl_success=false` 结束。[仓库报告：`experiments/ep51-failure-audit-20260920/EP51_FAILURE_TRACE_AUDIT.md`]

本轮与该历史运行的最早可观察关键差异发生在 MI 最终计划：

| 项目 | 历史物理运行 | 本轮 |
| --- | --- | --- |
| coupling | independent | independent |
| relations/shared view | empty/absent | empty/absent |
| resources | each `space:1` | each `space:1` |
| executor constraints | `[]` | `distinct-physical-entities(robot_a, robot_b)` |
| Controller | HTTP 202，创建并执行 Mission | HTTP 409，pre-Mission rejection |
| Habitat | 3000 steps，official false | 未执行，outcome unavailable |

因此本轮不能验证历史 Fetch 原因，也不能把当前失败归因于导航器、Stage2、初始位姿或 Habitat。本轮确认了一个更早的新阻塞：MI 输出与 deployment physical-identity evidence 不匹配。

## 9. 根因与责任边界

### 已确认

1. Planner 输出 physical distinctness constraint；Reviewer 批准。
2. 当前 B1 deployment 没有安装 physical-entity registry。
3. Control 正确拒绝无法证明 physical distinctness 的 constrained candidate set。
4. Runner 没有重复提交，归档器正确报告 system failure 和 benchmark unavailable。

### 各层责任

- **MI/Review：** 应仅在原始任务语义确实要求不同 Physical Entities 时声明该约束，并应拦截无依据的 distinctness。本次真实 Reviewer 没有做到。
- **Deployment composition：** 若未来存在合法 distinctness Mission，B1 deployment 必须提供真实、revisioned 的 endpoint/PhysicalEntity registry；不能伪造或从 Actor 名推断。
- **Control：** 本次 fail-closed 正确，不应削弱。
- **Node/Stage2/Habitat：** 本次没有被调用，对本次失败不承担执行责任。
- **Evidence infrastructure：** 正确保留单请求、409 原因和不可用 benchmark，不应把缺失物理结果归为 `pddl_success=false`。

### 尚不能确定

- 自然语言中的“two heterogeneous robots”是否在产品层应被 Interpreter 固化为必须使用两个不同 executor，还是只是环境描述与可用资源；当前 authoritative benchmark goal 本身不要求 distinctness。
- 对所有任务而言，最合适的长期修复是更强的 Reviewer/Planner约束、确定性 semantic admission，还是补齐 deployment registry 后仍由 Review 判定。单次模型样本不能决定通用机制。
- 修复前述 pre-Mission blocker 后，Fetch 是否重现 1000-step 导航预算路径。

## 10. 下一步建议

下一步应先做一个**不调用 Provider 的确定性离线反例**：用本次 final plan、同一 authoritative goal、Grounding Context 和 execution profile，验证 proposed admission 能区分“任务明确要求不同 physical executors”与“仅有两个 actors/goals”。该检查不能按 predicate 数量或机器人数量硬编码，也不能读取 live Node Inventory。

随后审查 deployment truth：如果 node-a/node-b 确实分别路由到两个稳定 Physical Entities，则为 B1 composition 提供 revisioned registry fixture，并用 Controller integration test证明合法 distinct Mission 能调度、缺失/不完整 registry 仍 fail closed。是否接入下一次正式诊断，应以 MI constraint 有语义依据和 registry 来源真实两项同时满足为条件，不能仅添加 registry 来使本次偶然草案通过。

只有上述 blocker 解决并独立验收后，才值得重新授权一次完整 Episode51 诊断，以采集 Spot 驻留、Fetch pose/rotation、distance、skill-step budget、finished sensor、`over_max_len`、高层切换、逐谓词 truth 与采集开销。本报告没有自动启动第二次实验。

## 11. 证据完整性

- `RUN_MANIFEST.json`：冻结 workload、代码/EMOS fingerprint、唯一请求、计划、Controller 结果和 protocol verdict。
- `EP51_FULL_CHAIN_DIAGNOSTIC_SUMMARY.json`：机器可读的已证实事实与未验证假设。
- `evidence/`：冻结输入、semantic evidence、final plan、Review、Controller rejection、provenance/verdict 和 preflight。
- `SHA256SUMS`：本报告分支中归档文件的摘要。
- `RAW_RUN_SHA256SUMS`：服务器原始运行目录所有文件的摘要；没有将 SQLite/WAL、大型运行日志或凭据上传 Git。

敏感扫描覆盖 key/token/password/Authorization/private-key 常见模式。归档不含凭据值、请求 Authorization header 或环境变量转储。
