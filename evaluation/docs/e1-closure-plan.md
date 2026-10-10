# E1 收尾计划与配置核验

本文件规定 E1 的收尾流程和启动检查，不记录真实实验成绩，也不替代
Population Manifest、Pair Validator 或 Formal admission。实际进度、机器路径、配置
指纹与结果保存在独立本机审计目录；真实实验结果不提交 Git。

## 1. 完成条件

E1 完成意味着预先冻结的任务集合已经执行、失败已经分类、对照证据可以复核，
并产出可追溯的统计报告。完成不要求每个 episode 成功，也不要求达到某个成功率。

| 交付 | 完成条件 |
| --- | --- |
| 任务范围 | 四类分别指定实际 config、dataset digest、episode/scene/seed 清单及官方目标；变体不混算 |
| 代码版本 | 每一新批量使用一个冻结提交、Prompt/源码指纹和可追溯二进制；运行中不替换 |
| 对照条件 | EMOS 与 RoboGuide 逐 pair 核对实际 workload、reset、模型条件及官方评测；证据缺失如实记录 |
| 运行覆盖 | 每个预注册条目都有原始结果或明确的未执行/基础设施失败记录，无静默跳过 |
| 统计 | 分开报告成功、官方 false、官方 unavailable、Formal 分母、benchmark 分母与 pair comparability |
| 失败分析 | 汇总 MI、Control、Node、Local EAIOS、Provider、物理执行和 harness 证据；无法定位的保留 unknown |
| 归档 | 原始中间结果、视频、Manifest、完整事件、配置和 SHA256 索引齐全；可离线重算 |

已有导航批量进入历史结果核验，不自动重复运行。历史与新类别若使用不同代码、
模型或 Local How，必须分别报告；不能合成“同一配置全 E1 成功率”。若论文需要统一
条件，缺口单独列明，不能更改历史 Manifest 或声称历史运行用了新版。

## 2. 范围与实施顺序

EMOS README 的四个类别是 Mobility、Perception、Manipulation、Rearrange，
类别名不是 episode 数量或唯一配置。先核对真实 YAML 的 defaults、dataset、agents、
PDDL 和原始步数，再冻结人口清单。仓库的
[历史 taxonomy](../e1/WORKLOAD-TAXONOMY.md) 是带日期的静态扫描，不能替代
当前部署核验；机器人类必须以实际 config 和加载结果为准。

1. **冻结搬运发布候选。** 审查当前 dev，完成必要 release 门禁，从提交创建干净代码视图，
   在该视图构建 Controller/Node，记录源码提交、构建命令、工具链及 binary SHA256。
   所有将进入对照的 profile、开关、预算和差异在调用前冻结。
2. **完成搬运批量与原生对照。** 使用已有 object.relocate 链路，清单在运行前固定。
   工程回归样本单独保存；不能把已知失败样本的少量回归当成总体成功率。
   若选择完整 dataset，新冻结条件下按完整清单运行，包括先前诊断过的 episode；
   这些是新实验条目，不替换历史结果，也不能按历史成败挑选。
3. **完成感知能力接入。** 先明确原环境 is_detected 的操作和观测契约，再核对节点注册、
   MI 输入、工具绑定和官方 verifier。只有导航能力不能宣称完整感知任务已支持。
   使用通用能力与离线回归，不为特定 episode 改 MI Prompt。
4. **完成整理部署接入。** 先核对实际机器人数量、角色能力与共享世界执行拓扑。
   当前一/二 endpoint 的搬运 profile 不能宣称支持原生三机器人 config；不能删除机器人
   或目标来套用它。扩大 Local EAIOS 部署拓扑前先核对既有中立契约，避免新增 Core authority。
5. **完成剩余类别批量与统一分析。** 各类别经过同一入口/证据门禁后一次性派发，
   汇总为四类结果包，保留版本差异、不可比 pair 和观测限制。

普通 MI 拒绝、Guard 拒绝、技能失败和官方 false 均进入结果后继续队列。
只有配置/身份漂移、reset 对照失败、证据归档问题和既定基础设施暂停条件才阻止新派发。
真实工程缺陷修复产生新提交和新批次身份；原批次保留，续跑清单明确引用已认领条目。
不通过重跑困难样本、增加模型次数或放宽 benchmark 目标提高成绩。

## 3. 每批配置记录

每个批量在独立目录保存一个公共配置 Manifest、Population Manifest 和启动核验记录。
一个配置来源渲染各 worker，禁止靠操作者记忆逐次拼环境变量。

| 配置组 | 必须冻结并核对的内容 |
| --- | --- |
| 身份 | batch/job/pair/run ID、完整 Git SHA、干净代码视图、Prompt/源码摘要、binary 来源/摘要 |
| workload | dataset 原始文件 digest、episode、scene、seed、完整 goal、task/config digest、机器人类型与数量 |
| MI | Interpreter/Planner/Reviewer/Repairer 模型、API 路径、reasoning、结构化 schema、恢复次数、每次超时 |
| Stage2 | requested model、实际 endpoint/API、原始 client 参数/重试/超时、返回 response model/fingerprint（缺失为 unavailable） |
| Local How | 每个选项明确 true/false；工具绑定、Guard、completion/feedback、导航变体和 passive endpoint 行为 |
| 执行 | 原始 simulator/skill budgets、单 Request 上限、观察/外层 wall/drain budgets、恢复策略 |
| 资源 | GPU 实际 owner/基线、worker 数、每 worker 独占端口/目录、峰值容量检查 |
| 归档 | reset、assignments、Stage2 调用/反馈、官方终态、Controller 事件、诊断/视频及损失计数 |

凭据只由现有授权的环境来源提供。Manifest 仅记录凭据是否可用和来源类别，不记录
凭据内容、前后缀、哈希、Authorization header 或敏感环境变量。公开 endpoint URL
不得携带 username/password、query token 或 fragment。

各模型路径独立核验：MI Responses 与原生 Stage2 Chat Completions 的 API 不同，
MI 成功不能证明 Stage2 配置正确。父进程环境存在也不能证明子进程用了该配置。

## 4. 已实现门禁与待核验项

搬运部署 [b1-deployment.json](../../scenarios/e1-shared-world-relocation/b1-deployment.json)
明确声明 v0.2、原始 Habitat config、max_steps、enable_relocation 和
relocation_completion_binding。legacy v0.1 保留兼容，但新 completion 批量不得依赖隐式默认。

现有自动检查：

- [b1_deployment.py](../src/roboguide_eval/b1_deployment.py) 冻结声明与 input 的原始字节摘要；
  v0.2 声明与环境 override 冲突立即拒绝。
- `b1-local-execution-profile-required.json` 将 run、input、deployment 与明确开关绑定。
- [relocation_preflight.py](../../integrations/habitat-local-eaios/habitat_local_eaios/relocation_preflight.py)
  在实际 reset/ONLINE 后、Controller/MI 前核对 child Local How、readiness、注册来源和
  step-zero 身份；启用 binding 时要求 Local How v0.8、feedback v0.4 和 loaded completion readiness。
- 缺失、旧 profile、冲突、错误类型或预算超限 fail closed，原始证据保留，明确归因 harness。
  这些检查不修改 provenance、Formal population 或官方 benchmark 规则。

发布对照前还必须核验以下证据，不能把本文件的清单描述成全部已自动实现：

- 由冻结源码构建并确认实际 executable 来源；单独 binary digest 不能证明 build commit。
- 使用既有 [accounting observer](../src/roboguide_eval/accounting.py) 记录实际返回模型身份、
  请求参数、调用耗时与 usage，按冻结上游等待预算转发；完整结束并 drain 后封存。
  原始 Stage2 日志缺少 response model 时不能从请求中的 model 名补造。
- 原生臂使用既有 native reset observer，逐 pair 比较真实机器人/物体/目标状态；seed 一致
  只证明请求条件，不证明实际初态相同。无法读取的 RNG/world 字段保持 unavailable。
- 使用既有 Pair Validator 与 [Fairness Ledger](../e1/FAIRNESS_LEDGER.md) 报告可比性。
  当前受控 Local How 有工具绑定、Guard、feedback/completion 等差异，采用 Protocol A
  完整系统比较；不声称 Protocol B 的“只改变组织层”。

## 5. 启动与封存 checklist

启动前：

- [ ] 精确批次版本/清单冻结；源码、Prompt、binary、dataset/config 指纹核对。
- [ ] 所有公共开关和预算明确；继承环境中的旧配置已检查，声明与 override 无冲突。
- [ ] 一个模板派生 worker 参数；GPU 容量、独占端口和全新目录核对。
- [ ] prepare-only 检查通过。它不启动服务、世界或模型，目录不能复用作正式 run。
- [ ] 正式 run 的真实 child activation 门禁通过后才创建唯一 production Mission Request。
- [ ] 原生对照与 accounting 路径已验证，实际 reset/模型身份/官方结果来源可核验。

运行与结束：

- [ ] 使用 [e1-batch](e1-batch.md) 的 claim/receipt 防止重启重跑已认领任务。
- [ ] 首个完整 pair 测容量与耗时，再按既有监督器规则启用双 worker。
  一张 GPU 一套隔离的监督实例可评估两个 worker；多 GPU 的编排不是自动支持四 worker 的声明。
- [ ] SUT failure 留证并继续；基础设施暂停和 502 continuation 采用预先冻结的既有规则。
- [ ] 超时、恢复和中断只继续观察原 request_id，不重新创建 Mission Request。
- [ ] 归档先于服务清理；accounting drain、事件尾部确认和诊断/视频损失检查完成。
- [ ] SHA256 索引、逐 pair 判定、失败 owner、各 population 分母和原始证据离线复核。
- [ ] 只发布代码与通用文档；本机配置、实验成绩和无关用户文件不提交。

## 6. 排期与停止优化的规则

下一里程碑是搬运全量结果包加已有导航结果复核，然后补齐感知和整理，最后交付四类 E1。
部署与证据核验优先完成；不能因等待某个 episode 成功而无限延迟批量。

ETA 按真实首个完整 pair 更新：`remaining_pairs × observed_pair_wall / active_workers`
只是初始估算；后续用整个已完成窗口的耗时分布并计入 Provider 长尾、排队、暂停和收尾。
不得用两个已知失败路径的成功回归耗时承诺全类别运行时间，也不得按空闲显存直接推算
四路可用容量。感知和三机器人整理尚未完成入口门禁时，四类总结束日期只能是有条件目标。

每个类别的完成门禁通过后停止普通性能调优，固定版本运行预注册集合；新发现的通用
正确性/证据缺陷单独处理。E1 的结果可以包含真实失败，最终报告必须说明优势和不足。
