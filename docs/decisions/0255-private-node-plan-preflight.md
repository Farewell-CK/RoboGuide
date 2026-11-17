# ADR-0255：runtime Pointer 与私有 Node 声明的只读计划预检

- 状态：已实现；限软件声明兼容性
- 日期：2026-11-17

## 问题

原 Node 编译器检查 Pointer 语法和步骤先后，却接受任意 `/invocation/...` 后缀。
例如 `/invocation/intent/parameters/destination` 能通过离线编译，实际运行时
`canonical_invocation_json` 只生成平铺的 `/invocation/parameters/destination`。
Controller 发布合同检查也不持有 Node 的私有 request mapping 与
`required_resources`，无法发现计划缺少参数、资源种类或私有必需资源无法被承诺的问题。

## 决策

1. Node 的生产编译路径检查 canonical invocation 的字段、标量/数组层级和
   RFC 6901 转义；按配置代际区分 legacy `capability_contract` 与 semantic
   `operation`/`objective`，仅 capacity 代际可读取 `resource_grants`。
   Execute 有 `attempt_id`，Status/Cancel 仅重建标量 `local_handle`；步骤响应仍使用
   原有先后和跨阶段拒绝规则。纯配置无法判定具体参数键或本地响应的内容。
2. `core/node-service/src/preflight.rs` 从显式路径冻结完整 Node TOML，复用生产编译器。
   每批 1–64 份、每份至多 1 MiB，重复 Node 身份、无法读取/解析/编译的来源均拒绝。
   来源摘要按唯一 Node 身份排序并绑定精确原始配置字节；不读取 credential 环境值，
   不启动 Node、journal、缓存、driver、网络探测或资源占用。
3. 对具体计划，逐 Task/Role 检查 exact operation、各阶段 mapping 实际读取的参数，
   以及 resource demand 的 kind/capacity/allocation mode/unit class。
   资源关系必须能在同一个 supplied catalog 内建立一对一覆盖，并覆盖该 operation 的
   每个私有 `required_resources` ID。另一 Node 的参数或另一 resource 的容量不能补足。
   仅以 null 占位检查已声明 resource array 长度，不生成 Control grant、ID 或 epoch。
4. CLI `roboguide-node --validate <node.toml> --plan <mission-plan.json>` 输出独立的
   `roboguide.node-plan-preflight/v0.1`，绑定 exact plan body 与 source digest。
   异构计划可重复 `--node-config <另一份配置>` 声明显式检查集合，不能重复 Node 身份。
   已知冲突输出报告后以非零退出；原无 `--plan` 的 conformance 报告代际不变。
5. `integration-server` HTTP listener 启动前，可选读取
   `ROBOGUIDE_NODE_PREFLIGHT_CONFIGS`（JSON 路径数组）。不接受请求体提供私有路径。
   显式配置时，`/v1/missions/validate-plan` 增加同一检查并输出
   `roboguide.plan-validation/v0.3`，保留 v0.2 所有字段及 published-contracts scope，
   另以必需 `node_preflight` 对象声明私有检查的 scope 与 source digest。
   未配置保持 v0.2；缺失私有来源不推断为已经检查。MI 显式识别 v0.3、保留来源到
   rejected-draft 反馈，旧消费者会拒绝不支持的代际而非忽略新增检查。
6. 返回最多 32 个 `{code,path,message}`，超出保留截断标记；路径只指向逻辑计划。
   HTTP 回复不发布私有 Node/owner/resource/path/endpoint 列表。
   配置错误是系统/部署失败，计划不兼容才是具体草案错误；不改变原模型恢复预算。

## 边界

这些检查只证明显式声明范围内的静态必要条件。它们不执行 Matching，不保证调度会选择
某个兼容 Node/ID，不验证 Actor placement、当前 readiness、资源可用性或物理能力。
原有 published-contracts 验证与提交共享的门禁继续有效；新增私有检查是可选的只读诊断，
不成为新的提交授权或持久化权威，真实提交和 Node 执行仍经过全部原有门禁。

透传整个 `/invocation` 不声明额外私有参数需求；未显式指定 execution intent 的历史 Role
不会被推断出新 operation。具体本地响应值、依赖未知值的转换、Artifact 实际字节/Prepared 状态、
Control 生成的 grant 内容和 execution generation 仍由原运行时检查。
来源冻结至进程结束；修改配置后须重启检查进程，摘要不宣称 live Node 已使用该配置。
本项没有扩展 replica supervisor 的环境白名单或 HA 部署/恢复策略。

不改变 MissionPlan、Node Protocol、State/checkpoint/event codec、资源承诺或恢复代际，
不增加 Node/worker，不运行 Provider/模拟器/硬件，也不授予物理成功或停止证明。

## 验证

Node 测试覆盖非法字段、嵌套标量、Pointer 转义、阶段/代际、实际缺参、私有必需资源、
容量、量化模式/类、跨 Node 证据拒绝和来源绑定。HTTP 测试通过真实 handler，验证
v0.2/v0.3、viewer 查询、逻辑诊断以及 checkpoint/event/command/journal/cache 零变化。
MI 测试检查闭合代际、来源摘要和负反馈保留；CLI 独立进程检查退出码与无副作用。

2026-11-17 本地验证：全工作区 all-targets/all-features Clippy 通过；Integration Server
306 项、CLI 真实子进程回归及 Mission 相关 100 项通过，Ruff、strict mypy、function-doc
与格式/diff 检查通过。Node 模块全量尝试发现 1 项既有失败：
`habitat_relocation_registration_retains_exclusive_resources_and_unknown_limits` 要求 capability
attributes 为空，而原始场景已声明 `location-access`/`supports-floor-transition`。
该失败在隔离的原始 HEAD `28156615` 上复现；排除此项后其余 147 项通过。
未运行全仓 pytest、Provider、模拟器、硬件或 HA/recovery process 验收。

## 相邻预检缺口复查（2026-11-17）

反例回归确认并修复以下同类问题：

- request target 只检查语法，没有检查其 `base` 和前序赋值的已知结构；已知标量父级、
  越界数组和先转换成标量再写入子字段会在本地调用前拒绝。
- 转换函数只检查名称/参数数目，没有执行纯常量或计划已知值的类型检查。
  `ToInteger` 同时修复 `2^63` 边界被浮点舍入后饱和为 `i64::MAX` 的错误；超范围值拒绝。
- Artifact path 未绑定本地 action、方向和实际静态 binding；声明 action 却没有对应
  data plane/binding 也会通过。现在还要求一次 invocation 的显式路径指向同一 slot，
  具体计划的 action/slot/map/revision/已知 output anchor 复用原 runtime digest 构造器
  的纯元数据校验，并独立核对实际选定 producer 的 LocalSystem owner。
- Memory 的 discover/export/原始 import outcome 可引用拼错或其他 workflow 的步骤。
  现在对 `/steps/...` 校验精确 workflow，保留既有 invocation 投影及 response-relative
  instance/query 指针，不猜测尚未返回的实际响应字段。
  discover/export/原始结果 query 的 `/attempt_id` 引用也会拒绝。先前关于 v0.10+ prepared import
  可用顶层 attempt 的描述在本轮运行时追踪中证实有误，现按下节修正；availability 和 use
  保留其实际 attempt 输入。Memory 的 staged 文件来自 `/invocation/staged_artifact`，
  不可借用普通 execution 独立填充的 `/artifacts/inputs/...` 或 `/artifacts/outputs/...`。
- 上一实现的 CLI `--plan` 分支只用了生产编译器，漏掉 conformance 的额外完整性检查。
  两种 CLI 模式现在对同一份已读取配置复用原 conformance 门禁，避免重新读源产生竞态。

`local_engine/mapping_preflight.rs` 仅保留临时的已知 JSON/未知形状，不存入 compiled
catalog，不修改原 Debug/identity/digest 表示，也不渲染真实 Control grant 或私有路径。
未知 step 响应、生成的 resource ID/grant/Group 和 local handle 不被替换为虚构值；
只从确定结构推导拒绝。合法的动态对象、按序覆盖、数组追加和转义名称保留原执行语义。
所有检查仍是静态必要条件，不建立运行或物理成功证明。

复查验证：六个新增反例在修复前全部失败，修复后与补充的正向/边界用例一起通过；
Node 163 项、Integration Server 306 项、CLI 真实子进程回归及受影响三个包的
all-targets/all-features Clippy、Rustfmt 和 diff 检查通过。上一节已在原始 HEAD 复现的
1 项既有 Node 测试本轮显式跳过；未重复全仓 pytest 或运行模拟器、设备与 HA/recovery gate。

## 扩大审计与修复轮次（基线 `78c2912e`）

### 覆盖表

| 入口 | 实际上下文及字段来源 | 静态校验与本轮结论 |
|---|---|---|
| Execute | `spawn_dispatch`：canonical invocation、原始 attempt、选定一个 Artifact slot、按序 responses | 编译器检查平铺字段/代际/阶段、Artifact direction/binding；具体计划检查标量、数组计数和同一 catalog 的资源覆盖 |
| Status / Cancel | 各自 `WorkflowContext::new`、标量 handle、自身阶段 responses | 不继承 Execute responses、attempt 或 staged paths；原运行时状态/停止门禁保留 |
| Health / Readiness | 空 invocation 的独立固定观测 | 编译器只允许部署常量；结果 Pointer 为 response-relative，不用 workflow-context 规则误判 |
| State / Peer observer | 空 invocation 的单步 GET 观测 | 固定只读路由、常量映射、响应 Pointer 语法；未知 payload 字段仍由真实观测验证 |
| Memory discover | 调用方 invocation，加 `memory_query`、本 workflow responses | 无顶层 attempt/handle/staged paths；结果仅可读实际步骤或 invocation 子路径 |
| Memory export | 调用方 invocation，加 `memory_manifest`、本 workflow responses | 同上；导出路径必须由 provider 实际返回并通过原 storage-relative 路径校验 |
| Memory import（包括 prepared） | `import_memory` 始终用 `WorkflowContext::new`，加入 manifest/staged path/原 invocation metadata | 本轮移除所有代际对顶层 attempt 的错误放行；仍允许 `/invocation/execution_id` 等原元数据，未添加新字段 |
| Memory continuity instance / query | instance 为空；query 仅有 `memory_import` 原始记录 | instance 仅常量；query 不得借用 attempt；typed outcome、instance identity 和 clock 仍由原运行时验证 |
| Memory availability | prepared invocation 加原始 import/manifest，通过 `with_attempt_id` 建 context | 实际 attempt 可读；GET、原 backend/current-content 校验保留 |
| Memory use control / query | prepared invocation 加原 use request，通过 `with_attempt_id` 建独立 context | POST/GET、固定 Unix backend、原 session/lease/current permission 均保留；无其他 phase responses |
| Artifact / 私有资源 | 具体 plan intent 与同一 catalog 的真实声明 | action/slot/map/revision/output anchor/producer owner 与 mandatory ID 覆盖均保持；引用字节、Prepared 状态、grant authority 不由静态预检建立 |
| CLI / Controller / MI | 显式本地源；Controller 不接受请求传入路径；MI 解析闭合的版本与来源 | CLI 完整 conformance；Controller 保留生产编译兼容入口。共同输入的 logical decision/issues/plan/source digest 对齐；专有 conformance 拒绝不伪装成计划缺陷 |

### 确认的缺陷与修复

1. 未知父值既可能是 object，也可能是 array。原部分求值对 `/0`、`/00`、`/+0` 和 append
   保留互相独立的字段事实，而实际 target setter 接受它们作为数组索引别名。反例中
   `/0=true` 后 `/00={}` 再写 `/0/child` 实际成功，旧静态检查却拒绝。
   现仅对未知父值撤销可能被数字别名写入覆盖的事实；真实已知 object 保留独立数字键，
   真实已知 array 保留原解析/越界行为。没有修改 source Pointer 或 runtime target 语义。
2. prepared import 的实际入口也使用 `new`，配置 v0.10 不能创造顶层 attempt。新增反例
   在修复前通过编译，现拒绝；availability/use 的实际 `with_attempt_id` 不受影响。
3. Memory result 只检查 `/steps/` 前缀，拼错根 `/step/...`、不存在的 attempt/handle 和
   空 Artifact 子路径仍可通过。现校验完整结果 namespace，并保留实际步骤和 invocation
   子路径；不把未知响应内容变为已知事实。
4. Controller 在持锁时对每个 Role 的全部 catalog 反复构造、格式化并哈希 workflow。
   现从原 digest 构造器抽出只借用声明的 Artifact 纯校验，保持原 digest 字节配方；
   兼容扫描找到同一 catalog 的完整匹配即停止。Controller 原权限与发布合同在锁内读取，
   独立私有检查和响应构造在锁外执行。错误分类和 32 条截断规则不变。
5. 既有搬运测试的空 attributes 断言与真实场景不符。改为精确核对两条楼层属性，单独
   拒绝虚构 `max-payload-grams`，保留原资源/owner/readiness/operation-support 断言。

### 来源、兼容性与剩余边界

- v0.3 `source_digest` 只绑定按唯一 Node 身份排序的 TOML 原始字节。本轮以相同 TOML、
  不同目录和不同有效 descriptor bytes 证实摘要相同。**NOT PROVEN**：摘要不能证明外部
  descriptor、解析后的 path 或 live Node 使用了同一配置。加强这一含义需要独立的版本化
  来源合同；本轮没有静默更改。缺失/损坏 descriptor 在启动编译时仍拒绝。
- 已加载 catalog 可在原 TOML/descriptor 删除后重复只读检查，不重新读源。配置变更须
  显式重启；冻结来源不等于运行时实际状态。credential 只编译环境变量名，不读取其值。
- 配置 1 MiB、最多 64 份和 32 条诊断保留原边界，分别测试精确边界及首个越界值。
  未新增 wire 字段、公共限制、profile 或持久化版本。
- **TEST GAP**：有限组合测试不是全部 JSON 形状的形式化证明。外部 descriptor 文件大小、
  本地 CLI plan 文件读取及受信管理员路径的阻塞 IO 无新增全局预算；本轮压力测试不是
  所有配置同时占满所有边界的 SLO 或硬实时证明。
- **NOT PROVEN**：实际响应 payload、Artifact bytes/Prepared 状态、实际 grant/session/lease、
  Node readiness、Actor placement、物理能力和停止结果仍必须经过原运行时门禁。

### 验证证据

- 三个新反例（数组别名误拒绝、prepared import 缺失 attempt、Memory 非法结果根）在修复前
  均失败；修复后通过。既有搬运测试修复前失败，本轮纳入完整回归，不再跳过。
- 固定种子 4096 组映射 × 6 个具体补全，共 24576 次实际 renderer 检查，含 2766 组静态
  拒绝、4736 次成功渲染；样本内无误拒绝。独立穷举比较 512 组资源配置/mandatory 集合
  × 4 组需求，含缺资源、容量和分配模式拒绝。
- 原 digest 字节配方对普通 operation 和四类 Artifact action 的兼容回归通过。
- 相同 debug 构建、64 份配置/256 个 Role：基线 private check 约 5.08 秒，优化后约
  40.7 毫秒；属于单次本地样例，无固定时间断言，不代表跨主机或负载 SLO。
- CLI 子进程和 HTTP handler 用真实监听 socket 确认没有 local endpoint connection；HTTP
  查询前后 checkpoint/event/command 不变，配置文件删除后仍使用冻结来源，journal/cache
  不产生。夹具自身初始化的 SQLite 文件与查询 effects 分开计量。
- 本轮不改 MI 回复结构/解析；未重复运行 Python 全仓、模型、模拟器、设备或 HA/recovery gate。
- 最终完整回归：Node 174 项、Integration Server 306 项、CLI 2 项全部通过，零失败、零跳过；
  三包 all-targets/all-features Clippy、Rustfmt、diff 检查通过。既有搬运属性测试已关闭。
