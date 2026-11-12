# 狗2位置接入后的 Memory 与 MI 接线缺口

后续进展：[起点 Memory 与 MI 软件验收](acceptance/2026-11-06-dog2-origin-memory-grounding.md)
已完成 opt-in 记录、正常 Node/Memory/Artifact 通路及 MI 内容读取代码，隔离实际进程
通过。Thor2 离线阻塞现场部署和真实模型规划，不能称真机接入已完成。

本文件承接[Controller 位置查询验收](acceptance/2026-11-06-dog2-controller-position.md)。
第一步已从现有正式 RG State 取得报告位置，后续不应再由外部操作端替系统查询
Robonix、保存坐标并手填计划。下述内容是待实现范围，不是已完成的系统能力。

## 当前证据与消费路径

| 层次 | 本轮核实的事实 | 后续需要补齐 |
| --- | --- | --- |
| 本机 Mapping | 提供本地 map_id、localization mode 和 map pose | 原位姿测量时间、质量及 frame/revision 关联尚未由本报告取得 |
| Node State | robot-state 为 class=node、type=robot，来源 robot-observer | 与物理实体的显式关联及规划可消费的受控空间事实 |
| Controller State API | 能按来源查询，保留 source epoch/sequence、接收时间和 TTL | 不把收到新缓存等同于重新测量，不凭节点在线推断位姿有效 |
| 当前 World State | 现场查询为零项 | 定义并注册允许的 robot pose / place 事实，保留来源和有效性 |
| Spatial Memory | 已有地图 manifest、不可变 revision、文件及副本机制 | 将现场已有地图与真实来源关联；目前本轮未发布对应 typed revision |
| Scene 地点 | 之前在 Robonix 中核实过公司门口等地点 | 使用本机只读接口组织地点记录，不复制先前文字坐标充作新事实 |
| MI State grounding | 只读取 class=world，且仅接纳明确允许的 payload schema | 部署允许的 schema、身份关联和数据选择，不放开所有 Node/Control 数据 |
| MI Memory grounding | 现有 reader 只获取 Global manifest metadata，不读取内容 bytes | 起点若保存在有内容的 Memory 中，现有 reader 不会自动取得坐标 |

## 位置与起点必须分开

当前位置随观察更新。某次请求的起点需要冻结具体证据，至少保留：请求/任务关联、
实体身份、地图引用与坐标约定、原位置、来源、原观测时间/接收时间、质量限制和
不可变记录身份。新位置或重启后的状态不能覆盖原起点；换地图后必须重新核对关系。

后续先评估既有 State/Memory 和请求上下文是否能承载该冻结记录及读写授权，再补齐
必要的版本化合同。不得把工作区的 inspection JSON 或聊天中的我当成运行期 Memory，
也不能为了适配现有 reader 而把所有任务私有起点无条件发布为 Global Memory。

仅有 map_id 字符串不足以代替地图 revision 和物理 anchor。完整地图 bytes 可走已有
Artifact/Memory 通路；MI 的普通规划上下文优先使用经授权的摘要与引用，不把整张
点云塞入模型。地图文件接入、地点语义、机器人定位是三项不同的验收。

## 建议的下一项实现及验收

1. 形成受控的空间事实映射和实体关联，明确当前仅有报告位置时允许规划哪些任务。
   数据缺失、过期、地图不一致时保留缺口，不猜坐标或变换。
2. 实现“记录本次起点”的正式系统操作，保存原证据和记录身份，通过原任务、资源及
   数据权限流程执行；查询可准确回溯原起点，后续位置更新不覆盖它。
3. 将允许的 World/Memory 证据接入已有 MI grounding，保留实际输入快照与来源。
   首次仅生成并审阅计划，不提交运动执行。现有请求流程的 AwaitingApproval 可作为
   审阅停点，但必须核实部署确实启用了对应策略。
4. 使用不含坐标的自然语言“查询狗2现在的位置，并记录为本次起点”，检查生成计划、
   实际执行与正式查询；随后再测试“返回本次起点”的计划生成，暂不让狗移动。

退出条件是由 RG 正式入口产生记录并由 MI 使用，有可追溯来源、失效与错图拒绝，
没有外部手填坐标。再进入单 Mission 的姿态/返程依赖图现场试验，保持 Robonix 的
本机导航、动作和安全职责。

## 代码入口

- [Controller State 查询](../../apps/integration-server/src/controller_http/view.rs)：原始来源、时间、TTL、epoch、sequence。
- [只读位置工具](../../integrations/robonix-task/robonix_task/position.py)：本轮已完成的诊断，未写入 Memory 或 MI。
- [State grounding reader](../../mission/src/mission/grounding_reader.py)：World/schema 选择与 Global Memory 元数据读取。
- [GroundingContext](../../mission/src/mission/grounding_context.py)：来源和新鲜度快照。
- [Spatial Memory](../../core/state/src/spatial_memory.rs)：地图元数据、来源和副本投影，不选择当前地图。
- [MI approval policy](../../mission/src/mission/approval.py)：实际启用后对生成草案进行审阅控制。
