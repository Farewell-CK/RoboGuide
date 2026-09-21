# Episode51 官方任务语义与 Stage2 执行契约审计

## 审计快照

本审计使用 RoboGuide `9ea39c2077308291569e67fd84c3fa40b7640599` 和实际部署的 EMOS
工作树 `/data/workspace/code/emos-baseline`。EMOS 工作树 HEAD 是
`e9501db45d634b087bf5d1a14228266685e8feeb`；该部署工作树存在既有修改，tracked diff 的
SHA256 是 `172268dd4cf44e8e50ba1d51de2f9f7a4ff5377b32d7aa96bfa21f1b77c0ee78`。
本次只读审计没有修改该工作树。

Episode51 的冻结输入是
`/data/workspace/code/roboguide-ep51-full-chain-diag-20260921T025132Z/b1-diag-ep51-seed40/b1-input-used.json`。
它绑定：

- episode `51`，seed `40`；
- scene `data/scene_datasets/mp3d/pRbA3pwrgk9/pRbA3pwrgk9.glb`；
- dataset revision `mobility_episodes_1`；
- dataset SHA256 `5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca`。

实际数据来自
`/data/workspace/code/emos-baseline/data/datasets/mp3d/mobility_episodes_1.json.gz`。
Episode51 将 `002_master_chef_can_:0000` 标为 `any_targets|0`。其当前 receptacle 是
`frl_apartment_basket_:0000`；episode 为同一对象提供目标 transform 和
`frl_apartment_wall_cabinet_02_:0000` goal receptacle，Habitat 将目标 transform 绑定为
`TARGET_any_targets|0`。episode metadata 还明确记录 `same_floor=false`、geodesic distance
`7.77636 m` 和目标高度差 `2.87407 m`。

## 官方谓词和成功条件

部署任务定义位于
`/data/workspace/code/emos-baseline/habitat-lab/habitat/config/benchmark/multi_agent/pddl/pddl_spot_fetch_mobility.yaml`：

```text
AND(
  any_at(any_targets|0),
  any_at(TARGET_any_targets|0)
)
```

`any_at` 在 `habitat-lab/habitat/tasks/rearrange/multi_task/domain_configs/fp.yaml:87`
声明为对一个 `static_obj_type` 实体的位置谓词。实际判定在
`habitat-lab/habitat/tasks/rearrange/multi_task/pddl_sim_state.py:417`：

1. 通过 `PddlSimInfo.get_entity_pos()` 读取参数实体的当前三维位置；movable entity
   使用刚体当前位置，goal entity 使用 episode target position。对应代码是
   `habitat-lab/habitat/tasks/rearrange/multi_task/rearrange_pddl.py:207`。
2. 遍历全部 Habitat robot id，读取每个机器人的 base transformation。
3. 计算目标点在机器人基座坐标系中的三维欧氏距离。
4. 任一机器人距离不超过 `robot_at_thresh` 时返回 true。当前部署阈值在
   `config_spot_fetch_mobility.yaml:99` 固定为 `2.0 m`。

谓词不要求特定机器人、不检查朝向、不要求抓取或搬动物体，也不要求两个机器人同时为
`RUNNING`。它要求两个存在量条件在同一个评测状态中同时为真。因此先到达的机器人若在
另一个目标成立前离开，联合终态仍会失败。PDDL 本身也没有声明两个不同机器人；但本
episode 的两个位置跨楼层且相距较远，一个机器人不能靠顺序移动在同一终态占据两处，
两个并发 mobility execution 是满足联合终态的自然实现，而不是额外的 distinct-executor
任务语义。

`any_targets|0` 是 movable entity，`get_entity_pos()` 读取它的**当前刚体位置**。因此
pick/place 会移动该谓词的参照点；它不是达到两个 `any_at` 所需的隐含实现步骤，还可能
使已经接近对象原位置的机器人不再满足 `any_at(any_targets|0)`。

官方 `PddlSuccess` 在
`habitat-lab/habitat/tasks/rearrange/multi_task/pddl_sensors.py:186` 直接调用
`is_expr_true(goal)`。当前配置 `must_call_stop=false`，没有额外 stop-action 条件。EMOS
暴露给模型的原始自然语言转换也将 `any_at(x)` 写成 “Any robot is currently at the
location of x”，代码位置是
`/data/workspace/code/emos-baseline/habitat-mas/habitat_mas/tasks/habitat_mas_sensors.py:227`。

## 两个 mobility.move 是否足够

**在计划语义上足够。** 官方任务文件自带的参考 solution 只有：

```text
nav_to_goal(any_targets|0, agent_0)
nav_to_obj(TARGET_any_targets|0, agent_1)
```

RoboGuide 的 canonical `mobility.move@v1` 定义为“移动到一个 semantic destination，
Local How 由本地系统保留”，见 `contracts/capability/v0.3/catalog.json`。因而两个独立
operation 分别保持目的地 `any_targets|0` 与 `TARGET_any_targets|0`，并由本地导航真正
把机器人保持在各自 2 m 阈值内，即足以使官方 AND 成立。

这个结论不等于“本地导航技能报告完成就证明谓词成立”。2026-09-21 的真实运行中，
agent 0 的 Oracle navigation 在 step 90 报告本地完成，但官方逐谓词读数仍为 false；
官方状态始终由 Habitat 判定。原始证据：

- `/data/workspace/code/roboguide-ep51-full-chain-diag-20260921T025132Z/b1-diag-ep51-seed40/evidence/diagnostics-steps.jsonl`；
- `/data/workspace/code/roboguide-ep51-full-chain-diag-20260921T025132Z/b1-diag-ep51-seed40/evidence/shared-world-summary.json`；
- `/data/workspace/code/roboguide-ep51-full-chain-diag-20260921T025132Z/b1-diag-ep51-seed40/evidence/authoritative-semantic-evidence.json`。

## RoboGuide 与 Local EAIOS 的执行边界

canonical operation 冻结 **What**：operation identity、semantic destination、objective、
Mission/Task/Role/Group identity 和已承诺资源。Local EAIOS 可以自主决定 **How**：

- 对指定 destination 的路径规划、局部避障和低层控制；
- 对同一 destination 的导航重试或重新规划；
- 环境实体到本地导航技能参数的确定性解析；
- 不改变目标的等待、停止和本地安全处置。

以下行为会改变 operation 的语义，不能被当作 Local How：

- 将 `destination` 换成另一个实体；
- 在纯 `mobility.move` / `mobility.navigate` 下执行 pick、place 或其他物体状态修改；
- 由没有 assignment 的策略产生物理动作；
- 通过跨执行器消息暗中创建未声明的协作依赖；
- 将非法动作静默转换为 wait，或代模型改写成“看起来正确”的动作。

若未来某个 canonical operation 确实需要操作物体或协作通信，应为该 operation 提供明确
的参数、能力要求和 adapter tool profile；不能扩大当前 mobility profile 来隐含授权。

## 历史运行中的首个契约偏离

归档的两个 Stage2 输入分别是：

- agent 0：`Move a robot to the semantic object any_targets|0.`；
- agent 1：`Move a robot to the goal receptacle position TARGET_any_targets|0.`。

文件是
`/data/workspace/code/roboguide-ep51-full-chain-diag-20260921T025132Z/b1-diag-ep51-seed40/evidence/subtask-agent-0.txt`
和
`/data/workspace/code/roboguide-ep51-full-chain-diag-20260921T025132Z/b1-diag-ep51-seed40/evidence/subtask-agent-1.txt`。
但是 agent 1
在第一次真实执行工具调用中选择 `nav_to_obj(any_targets|0)`，随后又选择 `pick`、
`nav_to_obj(TARGET_any_targets|0)`、`place` 和 `reset_arm`。完整原始调用保存在
`/data/workspace/code/roboguide-ep51-full-chain-diag-20260921T025132Z/b1-diag-ep51-seed40/evidence/chat-history/51/agent_1_action_history.json`。

这不是任务文本在 MI 到 Stage2 之间被换掉。上述 chat history 的 system prompt、规划输入
和第一次 observation 都保留了 `TARGET_any_targets|0`。实际差异是原始 EMOS Fetch
动作面同时暴露了 `nav_to_obj`、`pick`、`place`、`reset_arm` 等工具；模型在收到正确的
纯导航 subtask 后，仍自主选择了完整搬运序列。因而已证实的部署缺口是 canonical
operation 没有约束本地模型可执行动作，而不是 MI 下发了错误 destination。

因此该运行最早可确认的偏离不是 PDDL 与两个导航任务不一致，而是 Local EAIOS 执行器
没有保持第二个 canonical destination，并执行了未由 mobility operation 授权的物体
操作。这个事实不改变已归档运行的 `pddl_success=false`。

## Stage2 Contract Guard

`habitat_local_eaios.stage2_contract.Stage2ContractGuard` 在原始 model client 返回工具选择
之后、CrabAgent 处理消息或 HierarchicalPolicy 选择技能之前执行：

- `mobility.move@v1` 与 `mobility.navigate@v1` 只接受
  `nav_to_obj(target_obj=<exact destination>)`；
- 接受无参数 `wait`，用于保持当前状态或等待联合 episode；
- 同一目的地的重复 navigation 仍属于本地重试；
- 其他工具、错误目的地、额外/非法参数、未分配 agent 的非 wait 动作全部 fail closed；
- 不替换工具、不修改参数、不改 PDDL goal；
- 每次执行性工具选择在返回给 CrabAgent 前 fsync 到
  `evidence/stage2-contract-calls.jsonl`；
- 原始 EMOS client 若在解析工具参数或返回选择前抛错，写入脱敏的
  `model_client_error`，同时保留原始异常为执行结果；
- 证据写入失败也 fail closed，避免在缺少契约证据时继续物理执行。

失败通过现有 Local EAIOS `IntegrationError` 路径成为明确的 `local contract failure`。
shared-world episode 会停止，两个 Node 保留真实失败，而不会合成 benchmark 成功。

Guard 还在服务器实际部署的 Habitat Conda 环境中，以原始 EMOS `OpenAIModel` 类和冻结
的离线 completion 对象做了边界验证，全程没有 Provider 调用或 simulator step。验证
同时覆盖精确 destination 被原样返回，以及原始 client 在 `json.loads()` 处失败时仍保留
`JSONDecodeError` 并写入脱敏的 `model_client_error`。结果为
`REAL_EMOS_GUARD_OFFLINE_OK`。执行时 EMOS checkout HEAD 为
`e9501db45d634b087bf5d1a14228266685e8feeb`；部署文件
`habitat-mas/habitat_mas/utils/models.py` 的 SHA256 为
`a63f6af9e98731d80ae578be59d98dc317b7614de36fc8f8de5ab538e1d22b74`。
该 checkout 存在既有 benchmark instrumentation 改动，因此这里证明的是当前部署边界，
不是上游 clean commit 的逐字节复现。

## Spot 本地完成与官方谓词的判定差异

部署使用的 `OracleNavDiffBaseAction` 会先通过 `safe_snap_point()` 为语义实体选择可导航
落点，再以该落点的 XZ 平面距离 `< 0.5 m` 和朝向作为本地 skill 完成条件。官方
`any_at` 则直接计算机器人 base 到语义实体实时位置的三维欧氏距离，并使用 `<= 2 m`
阈值。两条判定链不是同一个事实。

历史 step 90 记录了 `oracle_skill_done=true`，但同一步两个官方谓词仍为 false。Spot
位置是 `(-0.45524, 2.91402, -1.46448)`；此时 Fetch 尚未进入 step 1001 的 pick，
kinematic 目标物体仍处于 Episode51 冻结变换给出的
`(-0.31774, 0.30787, -1.91269)`。两者 XZ 距离约 `0.469 m`，小于 Oracle 的
`0.5 m` 平面阈值；三维距离约 `2.648 m`，大于官方 `2 m` 阈值。因此已确认的直接原因
是本地完成条件忽略垂直距离，而 Spot 与目标物体仍有约 `2.606 m` 的高度差。

该次 `diagnostics-initial.json` 因旧版 NumPy 序列化失败而整体降级，也未保留 `_targets`
缓存，所以现有证据不能区分导航落点本身被 snap 到了上层，还是落点位于下层但 Oracle
仅凭 XZ 接近便提前完成。物理诊断 v0.3 为初始、逐步和终态记录官方 goal entity 实时
位置，并以明确的 private-source 标签记录有界的 Oracle 导航落点缓存；下一次运行可直接
比较机器人、导航落点和语义实体三者坐标。

## 下一次诊断实验

PDDL 与两个 navigation operation 的语义一致，因此下一次实验不应改目标、计划结构、
seed、步数或初始状态。建议沿用一次 Mission Request 的完整生产链，并显式启用/归档：

1. `stage2-contract-calls.jsonl`；
2. 修复后的 physical diagnostics；
3. 每步官方谓词和最终 `pddl_success`；
4. Control assignment 与 canonical invocation。

预先规定两种解释：若模型再次选择错误实体或 manipulation，运行应在第一次越界调用前
终止，结果归因为 Local EAIOS Stage2 contract failure；若两个首个工具调用都严格匹配
各自 destination，则继续观察 Oracle navigation、实际距离、skill budget 与官方谓词，
定位剩余物理导航问题。不得因前一种失败自动增加 Mission Request 或改写动作。
