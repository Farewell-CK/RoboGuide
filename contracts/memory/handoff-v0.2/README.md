# Group Memory handoff v0.2

`POST /v1/memory/handoffs` 接受 [request.schema.json](request.schema.json)。v0.2 允许在 `typed_map` 中携带完整的 `roboguide.map-manifest/v0.2`；有该字段时 child 必须是 Spatial，Control 还会核对原始 map 与 catalog projection、source/target membership、typed policy 和目标 generic Read/Import。身份来自实际
recovery-admin 证书及 writer gate 下的当前授权；请求没有 Node selector 或新资源权威。
source_execution_ids 是完整已绑定的当前集合，严格排序；两端 Group 属于同一 tenant。
deadline 是 Controller 本地绝对期限，最长五分钟，精确重试不续期。

`GET /v1/memory/handoffs?after=<handoff_id>&limit=32` 返回原 immutable parent/child lineage、
两端代际/attempt、阶段与原证据。tenant 过滤，最多 100 条/1 MiB；总历史最多 128 笔。

`POST /v1/memory/handoffs/<handoff_id>/commit` 的 body 为 `{}`，只允许原稳定 principal。
必须处于 ready 并重验全部停止/revoke、当前 State 和原接收证据；202 只说明持久接纳。
source 仍持有原 Control 资源和安全 hold，新 target 通过原准备及 Execute outbox 运行。
超时、策略、成员或原 attempt 变化进入 manual；没有 force-release/重新导入/自动重复入口。

commit 后原投递失败进入 manual 时保留原 commit 回执和接收证明。首批 receiver 全部退役
后，后续已接受 Task 可经新 Control 绑定/成员代际和独立 preparation/use 使用 child；
原 lease/probe/首次交接预算不复用。首批仍占有资源或撤销未确认时不得借用新身份绕过。

parent 与 child 均为实际已校验内容的 immutable manifest，新 child 只改变 selector、Group
scope 与创建时间，保留原 owner/provider/生产 provenance。父级关系完整存于 handoff 记录与
codec v27 事件。普通 Artifact alias 不获此例外；每块 CAS Read 仍验证实际证书、原 import
和独立目标权限。Spatial typed-map handoff 使用 v0.2 合同并另带完整原始 map manifest，
不能把 generic projection 当作完整地图来源；v0.1 只接受非 Spatial generic child。

Node Group 导出只从其原 journal 的活跃 operation context 调用配置固定的 discover/export；
Controller 再检查实际当前成员/provider/session。它不代表设备已定位、物理交接或全量复制。
wrapper v40 保留此权威并拒绝旧 writer；参见 ADR-0108。
