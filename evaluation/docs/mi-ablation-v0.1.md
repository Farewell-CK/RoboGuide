# MI 规划、审查与修复的配对组件消融 v0.1

状态：实现与开发验证阶段；正式语义评分、独立盲审和论文结论尚未完成。
本实验属于 Evaluation，不属于 E1/E2/E6，也不创建 Mission Request、Controller、Node
或物理执行。生产 MI Prompt、MissionPlan 合同及决策实现不因本实验改变。

## 研究问题与范围

在相同初始草案和冻结输入下，Reviewer 能否发现语义错误，Repairer 能否恢复忠实的计划，
以及新增调用、token 和耗时是多少？只验证 Planner/Reviewer/Repairer 组件；Interpreter
输出由预先编写的 resolved intent 固定，没有真实 Interpreter 成功率或物理成功结论。
这些是合成任务案例，不是从机器人实测得到的世界事实或部署可达性证明。

现有 production `config/mission.toml` 选 MissionPlan v0.8 和 Capability Catalog v0.3。
本轮按此显式 profile 测试，不宣称覆盖 v0.9–v0.11 的新增协议能力。Grounding 是明确的
空证据快照；具体对象/位置和条件来自用户任务的 resolved intent。没有 live inventory，
没有虚构 State export 或权威 benchmark goal。缺少部署 profile 表示未提供，不代表支持
任意执行拓扑。Controller 实际部署准入与物理 feasibility 不属于本轮评分。

## A/B/C 共享前缀

| 组 | 实际观察的生产路径 | 记录结果 |
| --- | --- | --- |
| A | Planner + 原确定性 normalization/contract/catalog/implementation checks | 初始候选可用或结构拒绝/不可用 |
| B | A 的同一候选 + 一次 Reviewer | Approved/Repair/Clarification/Rejected |
| C | B 的同一 Review + 原 route 允许的有界 Repairer，再 Review | Approved 或原路由终止/预算耗尽/不可用 |

A 的 `candidate_ready` 不等于 Reviewer approved 或 Controller accepted。B 的 Repair 只是
发现问题、请求修复；它没有执行修复。C 只消费真实 Repair 路由，Approved 即停，
Clarification/Rejected 不改成自动修复。未对缺失信息编造澄清答案。

Planner 结构失败最多 regenerate 两次，各生成周期上限三次调用。C 最多两轮 Repair；
每轮 Repairer 自己的结构失败同样最多 regenerate 两次。Reviewer 至多三次。
一条配对试次最多 3 + 3 + 2×3 = 12 个实际 Provider 调用。传输错误不触发重试或
新试次；未完成的 claim 不重新消费。真实调用数只计算一次共享前缀；每组逻辑成本计入
它依赖的整个前缀，不能相加当作账单。token 缺失保留 null。

这是同一条生产适配器链的嵌套策略观察，不是三套独立重新采样的端到端系统试验；
结论限于固定草案分布下审查/修复的增量作用。更高预算带来的收益不能单独归为机制优势，
需同时报告成本；本版未加入等调用预算的多次 Planner 基线。

## 输入、样本与模型冻结

- 开发集：`evaluation/mi_ablation_cases/development.json`，12 个案例，各一次。
- 评测候选：`evaluation/mi_ablation_cases/evaluation-candidate.json`，48 个案例，拟各三次。
  这是 12 个语义模板的四组参数变体，不能声称 48 个独立任务家族或严格未知领域泛化。
- 六个类别：对象/目的地、能力约束、DAG 先后、协作机制、资源/时间、证据缺失。
  所有案例及必要条件在真实调用前保存；没有使用模型输出或历史成功率来挑选案例。
- 开发例可用于修评测工具。正式结果不能用于改 Prompt、调标签后覆盖原始评分或追试选最好。
  若案例/标准确有缺陷，保留原版本，新版另行注册，相关原结果不能静默纳入新版。
- 公共模型为 `gpt-6.1-sol`，Responses、reasoning `xhigh`、max_output_tokens 16384、
  timeout 1200 秒、store=false。这与生产默认模型/4096 token/600 秒不同，是本组件试验
  明确冻结的配置，A/B/C 完全相同；不推断新模型身份一定对应固定 Provider 内部权重。
- 原模型配置名若仍是旧模型，不直接运行；CLI 在内存中替换所有三个组件配置。真实返回
  model 缺失/不同即停，原响应保留。凭据仅环境传递，headers 不归档，已知凭据字符串脱敏。
- 固定源码/Prompt/schema/catalog 摘要、案例摘要、shuffle seed 20261030、完整 slot 清单。
  每次启动试次前重核源码和输入，执行视图采用冻结提交。每个试次独立目录。

正式候选的语义评分规范仍需独立审阅。先完成开发验证、评分器反例检查和盲审准备，
再作正式集合冻结与调用；不能把 pending 候选自动当作已验收的 benchmark。

## 独立评分与证据等级

程序只检查预先声明的必要条件，读取真实 operation 参数、DAG、Context relations、
capability constraints、resource units、timing 字段。它不使用描述关键词命中证明忠实性。
检查通过仍为 `pending_blind_human_review`；未出现预期参数可能是错误，也可能是尚未识别
的语义等价表达，由盲审裁决，不能只按单一参考计划或固定 Task/Actor 数量判定。

人工复核需检查目标/约束完整性、对象/地点绑定、额外副作用、依赖必要性、证据是否被
编造、缺口是否正确暴露，以及生产满足策略。标注 correct/incorrect/uncertain，并提供
JSON path 和理由。两位独立评审可先各自标注，再保留分歧与裁决；在揭示组别前完成。
语义不确定样本不挪出总体分母。模型 Review 自己的 approved 不能兼任金标准。

`blind` 导出相同输入下去重的候选，隐藏阶段、模型 Review、评分输出和调用成本；
`PRIVATE-branch-map.json` 与公开给评审的 packets 分开。原计划内容原样保留，因此盲审是
隐藏组别元数据，并不保证评审无法根据内容猜测修复痕迹。对 Review issue 是否有依据、
action 路由是否恰当，还需独立核对 task/plan/Review，不能只靠候选标签推定。

正式指标：正确候选放行、错误候选放行、正确候选被误拒、原错误候选被修复、新引入错误、
正确澄清/拒绝、结构拒绝、基础设施不可用、未开始/未关闭、调用/token/耗时。
同时报告完整 planned 分母与特定条件分母；重复采样按 case/template 聚类，不能把三个
replicate 或四个参数变体当完全独立样本。结果揭示前确定区间与配对统计方法。

## 接线、自检与归档

实现位于 `evaluation/src/roboguide_eval/mi_ablation/`：
`runner.py` 仅编排生产 Responses ports；`evidence.py` 保存实际 envelope、完整 response、
计时与失败；`scoring.py` 提供私有必要条件；`blind.py` 导出盲审包；`cli.py` 冻结并消费
单个 slot。它不替代 `MissionRequestEngine` 或任何线上 admission authority。

每次调用保存 request（完整 instructions/input/schema/参数）、response、metadata 和错误；
结构失败另存 raw/normalized draft 与 validation_errors；每次 Review 绑定 plan digest
和同一 grounding context digest。重生成反馈使用原失败内容。私有 rubric 不进入 Provider。
原身份错误、结构失败、模型拒绝与 Provider/采集不可用分别保留。

测试使用脚本化 transport 只证明编排、合同解析、回执/预算/凭据隔离，不能证明模型语义。
覆盖共享草案/Review、合法与非法目标参数、修复结构恢复、澄清/拒绝路径、预算耗尽、
模型身份不符、502 不重试、已完成 A 不受后续错误重归因、重复 claim 拒绝、源漂移、
未关闭分母、盲审去重与未评分标签拒绝。新评分规则必须有反例测试。

```bash
uv run python -m roboguide_eval.mi_ablation.cli freeze \
  --repo /absolute/frozen/code \
  --cases /absolute/frozen/code/evaluation/mi_ablation_cases/development.json \
  --output /absolute/new/local/mi-ablation-development \
  --split development --repetitions 1

# 凭据从环境注入；run 只消费已登记的单个 slot，不自动继续或重试。
uv run python -m roboguide_eval.mi_ablation.cli run \
  --repo /absolute/frozen/code \
  --directory /absolute/new/local/mi-ablation-development --slot dev0-relocate-one-r1

uv run python -m roboguide_eval.mi_ablation.cli summary \
  --directory /absolute/new/local/mi-ablation-development
uv run python -m roboguide_eval.mi_ablation.cli blind \
  --directory /absolute/new/local/mi-ablation-development --output /absolute/new/blind-packets
```

并发仅跨独立试次，单条链保持原顺序。开发先一条真实 canary 核对端点、返回模型及证据；
未发现接线/身份/归档问题再消费其余固定开发 slot，最多两条链并行。真实模型误判是结果，
不作为加调用或改 Prompt 的理由。Provider/身份/归档故障停止派发并保留原 claim。

原始结果保存在仓库外，不 git add、commit 或 push；工具、定义和协议可随 dev 评审。
