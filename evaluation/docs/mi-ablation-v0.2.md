# MI 组件消融 v0.2：调用数匹配与候选集审阅

状态：工具已实现；任务集为待审候选，尚无正式语义成绩。边界沿用
[v0.1](mi-ablation-v0.1.md)，不修改生产 Prompt、MissionPlan、Control 或物理协议。

## A/B/C/D 比较

A/B/C 保持原共享前缀实现。D 必须在冻结 manifest 时显式启用 `--matched-planner`；
旧 manifest 不增加调用、分支或历史结果。`matched.py` 在 ABC slot 关闭后创建独立
`matched-planner/`，不覆盖 ABC。

令 n 为 A 初始 Planner 实际调用数（含结构恢复），k 为 C 关闭时实际调用数。
D 继承相同 A 候选和完整 n 次前缀成本，再进行 k-n 次独立 production Planner 调用。
每次使用相同公共输入与 Planner Prompt，无前次候选、Review、Repair 或私有评分反馈。
结构非法输出消耗一次预算，不额外 regenerate。固定选择最后一份结构合法候选；
后续全部非法时保留 A。选择完成后才附必要条件评分，不能挑语义分最高的一份。

- 无基础设施异常：C、D 逻辑调用数均为 k；物理账单共享前缀只计一次，
  `k+(k-n)` 最多 **21 次/slot**。
- 初稿结构恢复耗尽：无后缀，D 继承相同结构拒绝，零新调用。
- C 有 Provider/身份/非结构错误：D 为 not_run_source_unavailable，零新调用。
- D 中途 Provider/身份/采集异常：保留为 unavailable 并停止，不重试；即使此前有合法
  候选也不宣称完整匹配或成功。普通结构错误保留原输出并消费剩余原预算。

这是按 C 实际消耗匹配的无语义选择重采样对照，不是 best-of-N 或最强无Reviewer基线。
C 决定的 k 与任务难度相关，D 固定晚于 C；Prompt、上下文、cache、token及内部计算不同。
所以只声称匹配调用数，同时报告token/耗时，不声称等算力或普遍因果优势。

## 候选与阶段

保留旧 evaluation-candidate.json：12模板×4参数变体，不称48类独立任务。
新增 composition-candidate.json：24种合成约束组合，6个共同family，每类4例。
它在观察开发诊断后由作者设计，明确声明这一来源，不是外部留出集；不同模板也不证明
统计独立。不修改旧输入，不将人为变异计为自然Planner错误。

新组合覆盖交叉对象绑定、共享目的地、禁止额外动作、三目标；串联/汇合/分叉依赖；
角色局部载荷和跨层能力；真实协作和独立旁路；资源/期限组合；缺目的地、缺State合同、
矛盾窗口、不支持操作。每例有私有必要条件和完整人工审阅要点。

拟运行24例各一次：20个预期可表达案例、4个缺口/矛盾案例。后4个单列适当拒绝/暴露缺口，
不能仅因没有合法计划判普通任务错误。Interpreter不参与，不把结构拒绝称真实用户澄清成功。
是否正确处理缺口须复核原输出和路由。

任务审阅后冻结具体代码SHA、case摘要、顺序和预算。首次4个slot作为同一manifest前缀的
配置/身份/归档检查；正常模型失败保留。其余20个继续原清单，前4个不重跑、不重复计数。
如必须改工具或任务，关闭旧版本并另行注册，不能混合。上限24×21=504次，不是预计账单；
初稿直接Approved时通常3个物理调用/slot。本协议不自动运行Provider，CLI只消费显式slot。

## 独立评分与统计

人工标签约束成绩发布，不是额外模型调用授权。可以先采集冻结结果再独立评分，标签完成前
只报告结构/路由/成本，不报告总体语义成功率。任务和规则应由独立人员审阅歧义与可表达性。

blind导出A/B/C/D的输入/计划去重包；PRIVATE map不提供给评审者。每人分别复制空表，评
correct/incorrect/uncertain，并写JSON path与依据。检查完整目标、禁止动作、额外约束、
证据编造、满足策略、依赖必要性及等价表达；不能只看必要条件或Task/Actor数量。
精确数值检查只是提示，更强约束/别名/重组需要人工判断。两人先独立再裁决，保留原表与
分歧；作者与模型自评不能替代。隐藏元数据不保证评审无法猜测重复任务的变体来源。

完整planned分母、候选语义正确性和分支放行状态分开报告。B的Repair不是修复完成；
未放行的候选仍评语义。错误放行、正确误拒、修复与新增错误、正确拒绝、基础设施不可用、
未开始分别列出。缺口4例单列，不与20例暗换分母。独立标签可用时展示逐case C-D配对差，
再按6个family等权汇总；当前仅6个相关family、每例一次，报告描述性结果，不制造p值或
把变体当独立重复缩小区间。后续论文规模由任务来源和不确定性决定，不按结果临时加样本。

## 接线与复算

```bash
uv run python -m roboguide_eval.mi_ablation.cli freeze \
  --repo /absolute/committed/code \
  --cases /absolute/committed/code/evaluation/mi_ablation_cases/composition-candidate.json \
  --output /absolute/new/local/study --split evaluation --repetitions 1 --matched-planner
uv run python -m roboguide_eval.mi_ablation.cli run \
  --repo /absolute/committed/code --directory /absolute/new/local/study --slot <frozen-slot>
uv run python -m roboguide_eval.mi_ablation.cli summary --directory /absolute/new/local/study
uv run python -m roboguide_eval.mi_ablation.cli blind \
  --directory /absolute/new/local/study --output /absolute/new/local/blind
```

summary独立统计ABC已关闭请求、D新请求和未完成claim/request，不重复累计逻辑成本。
blind核对D的source-result摘要，不允许跨试次拼接。重复run拒绝；不能因D中断重放ABC，
需要调查原claim后另行登记续接。原始结果和人工标签保持本地，工具/候选/协议可随dev评审。
