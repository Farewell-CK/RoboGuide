# 通用能力选择性整合计划 · 2026-11-12

状态：五批代码已进入独立集成候选，最终组合与实际进程验收进行中；尚未推进 main 或发布部署。

目标是保留当前 chat，将远端通用接入、可靠性、数据治理和 MI 改进分批整合，
不把固定双狗任务、现场路线或人工到达发布器作为主线业务。
分支是否成为 main 祖先与其中有用代码是否已整合分别报告，不追求清空分支列表。

## 已核对来源

| 来源 | 固定提交 | 本次用途 |
| --- | --- | --- |
| 当前 main / chat | `28156615591318144b8cf54b9008270c251b8b25` | 每批从它及后续已验收 main 继续 |
| 通用基础汇合点 | `7fd7b2d15c569310e1a251231241741bc523f701` | 启动优化、热接入、委派、取消对账、origin Memory、在线发布 |
| 治理切点 | `516dfdf438b5b74526d043f294e64bb61c6a311e` | 完整 v3 / 恢复修复及采样隔离；尚无照片交接、固定巡逻 |
| 治理远端尖端 | `a699ab8ec75f82a010c364fec0dc6623f8b0c6d9` | 其最后三个照片交接提交本次暂缓 |
| Scene 增量 | `a9f1268b`、`f686ce22` | 仅抽取通用 producer / Connector / Mission 链 |
| Node Unknown 修复 | `99da6d5a69db771a4b5958299cc81374e39c00c8` | 仅抽取通用 Node / 配置 / 回归链 |
| host-owned objective | `200e7a4e`、`1c2afd67` | 显式选择的 MI Provider profile 与原调用/Prompt 来源 |

`7fd7b2d1` 包含 main 尚未纳入的 49 个提交；`7fd7b2d1..516dfdf4`
再增加 12 个。后者不是从 71 个提交中凭名称随意挑选，而是完整治理链的历史切点。
`516dfdf4..a699ab8e` 恰好是照片交接的 `f803800a`、`876720de`、`a699ab8e`。
固定巡逻预检从其后 `a9f1268b` 开始。

## 批次和交付边界

### 0. 先修清基线 CI

本次读取远端结果：main 的 Python job 与 Documentation 通过，Rust job 在测试阶段失败。
失败测试是 Node Service 的
`habitat_relocation_registration_retains_exclusive_resources_and_unknown_limits`。

现行部署片段已声明 `location-access=base-floor` 和
`supports-floor-transition=false`，旧测试却用 attributes 整体为空证明载荷未知。
通用基础已有对应修正：分别校验这些已声明属性，同时检查没有
`max-payload-grams`。先以该测试与实际配置核对并重跑；不为通过测试删除真实属性、
虚构 payload 或修改生产规则。Rust 后续进程门禁在这次 CI 中被跳过，仍须补验。

### 1. 合入通用接入与启动基础

从当前 main 建立独立集成分支，以真正的 merge 保留 `7fd7b2d1` 的完整历史。
它同时包含 `controller-startup-streaming`、`rg-hot-enrollment-maintenance`、
dog2 origin 与 local EAIOS 等共享祖先，无需重复 merge 它们。

保留完整取消/Unknown/资源归属、独立主机身份、在线采用与部署发布、原 origin
生产与消费者、流式 Catalog 恢复和显式 standalone storage v2。
已经提交的现场记录作为各自原时间和来源的历史保留，不转成新 main 的验收结果。

已在隔离工作树实际预演。唯一文本冲突为 README，保留 chat 与接入两段即可；
chat 独立包、SubmissionIntent、Request record/store 未被基础修改覆盖。
这只证明合并树可构造，不证明所有运行时行为兼容。

必须在这一批就补的兼容桥：origin Memory 会生成 GroundingContext v0.4，
当前 Request v0.5 schema 仅引用 v0.1/v0.2。应显式接纳实际受支持的 v0.4，
添加生产 reader → Engine/SQLite → Request v0.5 schema 校验，保留未来/篡改版本拒绝。
即使完全不合入双狗巡逻，这个缺口仍存在。

同时验证 plan-only 不提交、execute 绑定精确草案、审批独立、丢回包不重发、取消
原 HTTP Unknown 保留，以及 legacy Request 的原语义。

### 2. 整套合入治理与恢复

在批次 1 上 merge 至 `516dfdf4`，将 12 个治理提交作为完整验收单元。
保留 State v3 的原事件/值/引用/指标、Archive journal、readonly SQLite UDF 消费者、
Controller wrapper、授权、recovery v0.32 与完整 backup/verify/restore 修复。
不单独摘取“去重/清理”文件而遗漏恢复或原 reader 检查。

默认行为不变：未设置存储版本仍走原 Legacy；v2/v3 必须显式 standalone 选择，
复制部署拒绝未协商格式；retention 默认 evaluation，MI reported-posture verifier
默认关闭。合入源代码与启用现场存储、定时删除、部署机器人是不同操作。

分支明确未完成的项目继续保留：永久过期/完整引用覆盖、部分压缩与退休、复制 v3、
正式 24 小时稳定性。原 soak 只运行 1685 秒即中断，不能写成通过。
若迁移、旧 writer 拒绝、原事件/命令摘要、Archive 与恢复闭包不能通过当前源码验收，
该批独立留在候选分支，不阻塞已通过的通用基础，也不绕过检查合入。

### 3. 独立抽取 Node Unknown 修复

从 `99da6d5a` 选择性移植八个 Node core 文件的完整增量、任务片段中的
`unknown=["UNKNOWN"]`、ADR-0250 及必要说明/测试。
保留真实 status 的原 handle、detail、SQL journal 和资源锁；Unknown 不成为
Failed/Cancelled、不释放占用、不消耗错误的传输重试预算，更不重发 Execute。

这一批不依赖巡逻或 Scene；可以在批次 1 后提前进行，无需等待治理长期门禁。
选取提交记录原 Source-Commit 和实际文件清单；现场失败记录仍引用其原固定提交，
不把软件修复写成已在机器人上验证成功。

### 4. 抽取通用 Scene / physical World 接线

保留可配置的 `physical_observation.py`、`scene_targets.py`、Mission
`scene_grounding.py`、`grounding_reader.py` 的完整 profile 增量及相应合同/回归。
保留原字节摘要、精确实体与来源、有限几何、Controller 保留时间和本机采集耗时；
Memory 等后续采集结束后还要在冻结前复验，不能只取首次检查。

producer 与 consumer 使用同一明确 schema。Scene v0.2 的 `definition_json` 原 UTF-8
字节是唯一目标定义；不以舍入、epsilon 或重序列化来绕开摘要。报告位置与地图名称
不升级为定位验证、共享坐标或到达事实。

本计划的基础是 **516dfdf4，而非 a699ab8e**：

- 将 Scene/physical 导入、参数、只读路由和原采样工作器接线移植到较早 Connector。
  不整份覆盖成带照片任务类和路由的较新文件。
- 较早基础没有 `test_connector_photo_routes.py`；仅移植其中通用 World HTTP 用例，
  放入独立 World routes 测试，不顺带引入 photo jobs/imports。
- 固定 `navigation_scene_simulation_gate.py` 依赖双狗 assignments，不能作为这批通用
  Scene gate；应验证选中 producer → Controller JSON → MI 的真实字节链。

### 5. 整合 host-owned objective

在已验收通用基础上整合 `200e7a4e` 与 `1c2afd67` 的新增逻辑，保持 profile opt-in，
缺省仍为 echo；Provider DTO、宿主组装、Planner/Repairer/runtime/replacement、
Prompt identity/provenance 和原预算必须一起保留。

不能用旧分支文件覆盖新 main。尤其保留：无 `max_output_tokens` 政策、Catalog
违反保留为 rejected draft 的恢复逻辑、chat 的 SubmissionIntent 和独立风险审批。
新增旧示例本身还带输出上限，须同步移除；旧 host Catalog 测试须按当前拒绝合同核验，
不为适配旧测试回退生产行为。

host objective 的旧 ADR-0231 与接入决策冲突，应分配新的未占用编号；chat 的
ADR-0251～0254 和原接入决策保持。profile 不会自动变更已冻结的历史模型/Prompt 来源。

这一批也可在批次 1 后独立进行，不需要启用治理或 Scene。

## 本次不整体引入

| 内容 | 处理 |
| --- | --- |
| 固定 `patrol.py` / `patrol_grounding.py` / `navigation_preflight.py` | 两狗、茶水间/办公室及固定分工留在场景分支 |
| 人工 arrival verifier / 注册器 / arrival plan 与配置 | 整个固定现场闭包暂缓，不仅移动两个常量就宣称通用化 |
| 两节点照片交接三个提交 | 原分支保留，后续按 adapter/scenario 独立评估 |
| E1 历史发布三个提交 | 不阻塞运行时；保留冻结 cohort、报告和原哈希，当前不重生成 |
| H-CoRE 四个提交 | 独立集成/场景批次；先处理旧配置和外部资产依赖 |
| Android / NPU | 同一 app 的分叉，有 532 个重叠新增路径；先选维护基线，比较独有改动，不先后整体 merge |
| GuideAssistant / Flutter / 历史应用树 | 与 core 无共同祖先；独立仓库或显式客户端子目录，不能混合根目录 |

不删除或重写原分支、失败回执、实验记录。暂缓内容未来有明确用途时可单独整合。

## 检查和验收安排

每批先跑对应窄检查，失败时扩大；不在每次局部修改后重复全仓测试。

| 批次 | 必须覆盖的检查 |
| --- | --- |
| 基础 | Node/Control/Controller 受影响测试；原取消/热接入/在线发布/重启进程门禁；origin + Request v0.5 + chat 执行回归 |
| 治理 | State/Archive/引用/指标与 recovery 测试；真实迁移、旧 writer 拒绝、完整 backup/verify/restore、故障与原 Unknown；新存储拒绝复制入口 |
| Unknown | Node 编译/状态映射、原 handle、重复查询、重启与占用测试；配置与 digest 兼容、真实旧配置读写边界 |
| Scene | 原字节穿过 JSON 链、错实体/来源、过期、地图变更、浮点、冻结前预算及只读路由；缺数据仍 unavailable |
| host profile | 全部 Provider 生成入口、预算/Prompt 漂移、Catalog 草案恢复、旧默认、Request 恢复和 chat plan-only |

跨 Domain/配置/codec 的批次尽早跑 all-targets/all-features 编译或 Clippy，查全调用链。
最终候选统一跑 Python/Rust 合并检查、文档、wheel 和当前源码所需进程门禁，记录精确 SHA。

独立 `robonix-task` 没有加入 chat wheel，也不在默认 pytest/mypy 的完整发现范围内。
必须按其 README 单独加载 grpc/protobuf，执行 unittest、严格类型和所需过程验收；
不能用根目录全套 pytest 通过代替这个适配器的检查，也不把机器人依赖加入 chat 默认安装。

实施中的真实结果记录在最终集成验收文档。上述检查仍是交付门禁，不能把原分支结果当作
新候选结果。没有付费模型调用、设备动作或现场存储迁移；合并后的结果绑定当前候选源码。

## 执行方式和当前预演

1. 固定每个源 SHA、原 main、当前工作区与证据摘要；使用独立集成分支/工作树。
2. 每批单独提交；原历史可以完整保留的使用 merge，选择性移植用清楚的源提交和文件
   清单。README/AGENTS/V2 同时保留各自职责，不整份取某一侧。
3. 交付表标记“完整合入／选择性移植／暂缓”，不使用 ours merge 伪造分支覆盖。
4. 只将本批已通过的候选推进 main；现场启用和未完成稳定性另行管理。

实际集成工作树：`/tmp/rg-integration-plan-20261112-dhbioz6i/preview`，分支
`integration/core-selective-20261112`。初始预演与实施结果分别保存在该验收根目录及
`implementation/` 子目录，固定源 SHA、选择性移植提交与最终结果分别记录。
