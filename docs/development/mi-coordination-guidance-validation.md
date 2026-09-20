# MI 协作模式指导与离线验证

状态：实现与离线回归；真实模型遵循率尚未验证。开发基线：
`02f164b8831be873eedd923bc28d737301b8453e`。

## 修复依据与边界

历史审计报告位于原工作区
`experiments/ep51-history-vs-current-mi-audit-20260920/EP51_HISTORY_VS_CURRENT_MI_AUDIT.md`。
本次逐项核对报告中的两份真实计划、三份 Provider 拒稿和四份 Interpreter assessment，
均与原始归档相同。两份合法计划的 Context 都是 `independent`，没有 shared view 或 execution
relation；无 DAG 依赖的任务仍能在资源允许时并行。

最近三份拒稿都选择 Context 级 `concurrent-cooperation` 且 `relations=[]`。第一、三次缺少
shared view；第二次新增 pose/execution view，但 pose 的 export/schema 为 null。
Task 级 `independent` 不消除 Context 级机制义务。补上一个 execution-only view 仍不能
弥补不存在的 execution relation，也不能替代实际需要的位姿共享。

历史 Interpreter 输出并不相同：A/B/D 明确允许同一或不同机器人满足两个到达条件；C 只明确
分工和协调交给系统。四次都没有额外要求搬运或交接。A/D 都要求联合终态，A 仍生成了合法的
independent 计划。这些证据支持补齐长期存在的指导缺口，不能证明某个词或随机性就是模型
选择错误模式的唯一原因。历史完整 Provider 请求没有归档，不能宣称新旧模型输入完全相同。

本次只修改 Planner、Repairer 的契约指导。它们明确区分独立并行、顺序交接、执行期合作和
紧密合作，要求 relation/view/peer channel 具有真实语义依据，并解释 execution 与 pose/velocity
绑定的不同来源。规则不含 episode、机器人名称、目标实体或机器人数量特例。

Planner 初次生成与预校验重生成共用同一 Prompt。新增恢复指导要求重新检查真实协作需求，
而不是只机械补齐第一条错误。Repairer 只接受通过结构校验、但被语义 Review 要求修复的计划；
同步该指导防止语义修复重新引入相同错误，不改变其权限或调用边界。共同规则块用测试防止漂移。
Reviewer 仍按原有 grounded constraints 检查语义；没有重写其 Prompt。

MissionPlan v0.8、Provider schema/normalizer、校验器、Control/Runtime、恢复预算及协议均未改变。
可空 Provider 字段是既有 strict DTO 对可选字段的表达；null 会被归一化，但并不使缺少必要
State contract 的 pose/velocity binding 合法，因此无需改动 schema 来修复这次指导缺口。

## 三层离线证明与未证明部分

| 层次 | 验证内容 | 能力边界 |
|---|---|---|
| Prompt 交付 | 初次 Planner、regenerate、Repairer 真实适配器发出的共同规则一致；恢复保留同一 intent/context | Fake transport 不证明真实模型会遵循 |
| 结构与 MI 完整校验 | independent 保留显式 DAG；真实 active dependency 配合法 execution-only view；缺 view/relation、非法 pose/velocity、缺 peer channel、DAG 冲突继续拒绝；历史 accepted/rejected 结果不变 | 合法语法不证明真实部署可用，也不证明自由文本目标被忠实保留 |
| Control 实现支持 | 现有 Rust 测试验证独立任务的资源占用、并行分配、可恢复资源短缺、顺序复用；精确 export/schema/freshness 的 Group view 与 Runtime execution 字段；不支持的 relation 在进入 Control authority 前拒绝 | 不运行真实 Node、Provider 或 Habitat，不测 benchmark outcome |

**不能将 G 类场景宣称为已经完全由确定性校验强制保证。**
Prompt 明确禁止编造 export、删除必要状态需求、强制降级 independent 或 execution-only；
离线测试证明同一真实位姿需求与缺失合同的草案，在重生成时仍保留原文并被拒绝，没有隐藏改写。
但现有结构校验只能检查非空 export/schema，不能证明任意非空字符串来自可信部署，也不能判定
模型删除了自然语言中的真正位姿需求。合法字段的正例只证明结构与实现类型支持。

当前 Planner 返回值没有新增的“缺 State contract”类型。指导要求保留需求和显露证据缺口，
沿用既有 Review 或拒稿/失败路径；不能据此承诺新增了专门、机器可判定的 gap 路由。
以后若要提供更强保证，需要独立设计部署状态契约的准入/来源校验与语义 Review 证据，
不能在本次 Prompt 修复中读取 live inventory 或改写任务来代替它。

## 回归证据来源

[coordination-history.json](../../mission/tests/fixtures/coordination-history.json) 是单元测试输入提取，
不含实验指标、凭证或实验结果裁决。保留原始文件路径、原始文件 SHA256、计划/拒稿 canonical
digest。测试运行只读仓库内 fixture，不依赖服务器绝对路径。

- A：`/data/workspace/code/roboguide-stage2-provider-_hyz84nd/b1-preflight-episode51-seed40/b1-request-record.json`：
  `plan`、`draft_digest`、`assessment`、`grounding_context`。
- B：`/data/workspace/code/roboguide-b1-resource-ready-ns8nlo34/b1-request-record.json`：相同字段。
- D：`/data/workspace/code/roboguide-ep51-diag2-20260920/b1-diag-ep51-seed40/b1-request-record.json`：
  `assessment`、`grounding_context`、`dialogue`、请求/任务身份。
- D 拒稿：同目录 `b1-request-observations.json` 的 `rejected_drafts[0..2]`：
  完整 `provider_output`、raw/normalized digest、原始错误。normalizer 结果与已存 digest 比对，
  没有重新编写历史输出，也不把静态 scenario plan 作为模型历史输出。

A/B 原始 Provider DTO 未保存；测试直接使用最终 canonical plan 经过
`_validate_plan_output` 的 identity/profile/support/physical grounding/Catalog/policy 全链。
D 使用真实 Responses adapter 和 Request Engine，通过 fake transport 按原顺序返回原始 DTO：
两次重生成耗尽后仍 Failed、plan=null、draft_revision=0、零 Controller 提交、零库存读取；
每份拒稿原文、错误、digest 和冻结 context 在 SQLite 重载后仍一致。

## 验证入口

新增回归：

```bash
uv run pytest -o addopts='' -q mission/tests/test_coordination_guidance.py \
  mission/tests/test_coordination_history.py mission/tests/test_planners.py \
  mission/tests/test_provider_mission_plan.py mission/tests/test_models.py \
  mission/tests/test_rejected_draft_recovery.py
```

Control/Group view 的既有确定性支持测试：

```bash
cargo test -p orchestration
```

该包覆盖 `shared_world_endpoint_resources_flow_through_control_ownership`、
`group_shared_view_uses_exact_export_schema_and_freshness`、
`unsupported_relation_never_reaches_control_authority` 等相关路径。
结合新增 Python 测试证明 independent 不会隐式添加 DAG 依赖，不能把仅通过 Python parser
等同于实际 Control 调度成功。

提交前还运行仓库全量 Python 测试及 AGENTS.md 的 Ruff、strict mypy、function-doc、diff 门禁。
本轮不调用真实 Provider、不启动 Habitat、不提交真实 Mission Request，不能对下一次模型输出
是否稳定选择正确模式、物理协作能否完成或官方 benchmark 成功作出承诺。

## 本次实际结果

- 新增 30 项参数展开后的回归；上述 MI targeted 集合 118 passed。
- 全量 Python：815 passed。
- Rust `orchestration`：58 passed；同包 `clippy --all-targets -- -D warnings` 通过。
- Ruff format/check：Mission、service、quality tools、两个集成适配器、evaluation 全部通过，
  format 检查 174 个文件。
- AGENTS.md 规定的 **分组** strict mypy：Mission/集成范围 73 个文件通过；
  evaluation 范围 51 个文件通过。
- Python function-doc 检查、`git diff --check` 通过。

额外扩大的“MI + evaluation 同进程”strict mypy 检查不是全绿：基线和本分支均为同样
20 个错误，逐条诊断一致，集中在 evaluation 的 `mission_front/recording.py`、
`mission_front/runner.py`、`mission_front/grounding.py`、`test_eval_mission_front.py`。
显式检查 MI 源码与 evaluation 单独检查时对 MI import 的处理不同，会暴露既有跨包类型问题。
使用独立的未修改 `02f164b` worktree、同一个 mypy 二进制和 `--no-incremental` 对照确认，
未将这些错误归为本次新增，也没有修改无关模块来消除它们。
