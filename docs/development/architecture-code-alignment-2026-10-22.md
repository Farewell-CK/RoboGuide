# V2 正文与当前代码对齐（2026-10-22）

本次文档基线：`main = 495a6d274f6f930016d97ec5514124876e539cfe`。
独立工作树：`/tmp/roboguide-docs-20261022`；不读取或消费其他工作树的未提交修改。
结论：V2 的核心权威仍适用，但正文混合了早期设计、不同实现切片的“当前”说明和前插的 ADR 更新，
因此存在互相矛盾的能力描述。代码已明显领先这些早期段落，需要正文整合而非继续追加更新段。

## 已修正的确定性落后

旧原文保留在[历史快照](../architecture/v2/architecture-history-2026-10-22.md)。下表比较冻结主线源码，
不是本次重新运行软件/设备验收的结论。

| 旧正文表述 | 当前主线证据 | 修正口径 |
| --- | --- | --- |
| Node Protocol 尚不包含 selective-import，consumer selection 和 durable acceptance 属于后续工作 | [memory_import.rs](../../apps/integration-server/src/application/memory_import.rs)、[Node memory operations](../../core/node-service/src/engine/memory_operations.rs)、[Node preparation service](../../core/node-service/src/service/memory_preparation.rs) | 已有 selective import/outbox/receipt 与 Prepare；接收方必须由当前 binding/Task/attempt 决定 |
| Group-scoped distributed authorization/handoff 尚未形成 | [memory_use.rs](../../apps/integration-server/src/application/memory_use.rs)、[memory_handoff.rs](../../apps/integration-server/src/application/memory_handoff.rs)；ADR-0106/0108/0126 | 已有 use/revoke、逐 provider 对账与双 Group handoff；scope/权限/原预算不因复制而扩大 |
| Active Map 选择、删除/GC 和安全策略全部后置 | [Active Map](../../integrations/memory-provider/roboguide_memory_provider/active_map.py)、[reclamation](../../integrations/memory-provider/roboguide_memory_provider/reclamation.py)、[content_references.py](../../integrations/memory-provider/roboguide_memory_provider/content_references.py) | 原 provider map/revoke/reference/tombstone 门禁已有；仍不宣称全外部引用 producer 覆盖或物理定位 |
| 运行期 MI replacement revision 尚未实现 | [plan_revision](../../apps/integration-server/src/application/plan_revision/)、[runtime_replanning.py](../../mission/src/mission/runtime_replanning.py)、[runtime_work_store.py](../../mission/src/mission/runtime_work_store.py) | 已有原目标、claim/nonce/discovery/Review、scoped Memory 和新 Group 的软件路径；模型仍只提供候选 |
| Replica profile 限定静态三成员 | [controller-replication/lib.rs](../../core/controller-replication/src/lib.rs) 的 add_learner/change_membership、[supervisor](../../apps/controller-replica/src/main.rs) | 默认 standalone，显式 profile 已支持动态成员和原 child 排空；在线内容/升级/独立故障域仍需验收 |
| Group 在调度提交后才创建，一个 Mission 长期等同一个固定 Group | [mission/checkpoint.rs](../../core/orchestration/src/mission/checkpoint.rs)、[mission/revision.rs](../../core/orchestration/src/mission/revision.rs) | 完整计划接纳时建 Group/TaskExecution；显式修订可退役原 Group 并建新 Group，保留历史 |
| Node Contract/config/checkpoint 的不同早期版本都写作“当前” | [source-derived versions](../../website/site_support.py)、[content baseline](../../website/content-baseline.json) | 正文解释各版本的独立职责；当前数字由源码参考页与冻结 manifest 核对，不再逐段重复旧号 |
| units 不是可分割 quota；正文缺少 tenant quota 接线 | [quota.rs](../../core/control/src/quota.rs)、ADR-0140/0141；[process_jobs.py](../../integrations/memory-provider/roboguide_memory_provider/process_jobs.py) | 逻辑 tenant/category quota 已接 Commit/release；独占资源、divisible allocation 与本地 OS quota 分开 |
| 原 stop authority 一直写作实施中 | [safety_stop.rs](../../apps/integration-server/src/application/safety_stop.rs)、[process_jobs.py](../../integrations/memory-provider/roboguide_memory_provider/process_jobs.py) | 已有原 scope/receipt/terminal/fresh evidence 和软件 OS wait 门禁；设备停止仍不从软件证明推导 |

## 文档结构调整

- [V2 现行架构](../architecture/v2/README.md)按定位、职责、对象、承诺、State/Memory、本地执行、恢复和剩余工作重组。
- 新增英文对应正文，保持同一能力和证据边界；原公共章节/锚点保留兼容。
- 原全文作为同目录历史快照保留，原相对引用保持可用，不覆盖旧验收结论。
- 网页正文预渲染 Mermaid 为 SVG，默认直接显示完整图；提供放大和源码，禁用 JavaScript 仍显示架构图。
- 当前正文不再混用“阶段未闭合”与“该阶段完全没有实现”；每项区分主线代码、组合验收与部署证明。

## 另一开发会话的已提交代码

本次只读观察到 `codex/nonrelease-remediation-closure` 已提交到
`6c5a099bcda9ca1fce9f7039b0c4f53fed5e514b`，此时未包含在上述 main 中。
该分支另有 ADR-0146–0154 和 imports/snapshot/catalog/lineage/Artifact maintenance/archive 引用与
原 unpublished-copy abort/cleanup 代码。源文件、原 owner/operation 与存储 fence 已进一步推进，
因此旧的全分支统计不能当作当前状态。

本次没有合并或改写该开发线。它们仅作为“已提交开发分支”的证据记录，不能提前写成主线已验收：
[原 archive abort 提交](https://github.com/Farewell-CK/RoboGuide/commit/6c5a099bcda9ca1fce9f7039b0c4f53fed5e514b)。
后续合并应核对本页剩余引用 producer/在线内容恢复范围，再更新 V2 当前章节和源码基线；不能只按 ADR
编号增加就宣告 F4/F5 或 SoftwareComplete。

## 本次验证范围

核对源文件和已提交调用链，运行文档构建、静态发布检查及浏览器显示检查；不启动 Controller、Node、
Local EAIOS、Habitat、外部模型或设备。本次不重新宣称任何历史实验、软件进程 gate、物理安全或性能结果。
