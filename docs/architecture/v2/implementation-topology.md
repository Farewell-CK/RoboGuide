# 当前软件实现与部署拓扑

更新：2026-10-19。对应全部非发布软件整改计划；后续目标不视为当前能力。
节点、LocalSystem、能力、操作、State export 和 Memory provider 均由部署配置和正式注册提供。
未接设备的部署可以拥有零个 Node；系统不能自行生成设备或能力。

```mermaid
flowchart TB
  Client[证书主体 / tenant / 幂等键] --> MI[Mission Service / MI / Request + provenance SQL]
  ID[Control 持久身份 / 授权 revision / tenant / 测量] --> Entry
  Entry --> WIT[实际 leaf / 当前委托 / 事务写锁内复核]
  WIT --> SQL
  ID --> PROTO
  ID --> ART
  MI --> Entry[Controller 用户与高权限入口]
  Entry --> ORCH[Orchestration / DAG / Mission hold]
  ORCH --> CTRL[Control / commitment / binding / calendar]
  CTRL --> RT[Runtime / attempts / durable intents]
  RT --> PROTO[Integration / Node Protocol]
  PROTO --> NODE[配置接入的 Node Service]
  NODE --> LOCAL[部署侧 Local EAIOS / 操作 / 即时安全]
  LOCAL --> OBS[注册中声明的 State / safe-state export]
  OBS --> PROTO
  PROTO --> STATE[来源联邦 / receive time / TTL]
  STATE --> BELIEF[显式 Belief producer / 原始 lineage / 非续期 TTL]
  BELIEF --> GATE[Control 来源策略 / Match Commit Bind Rebind Readiness]
  STATE --> GATE
  GATE --> CTRL
  VERIFY[独立 verifier / 当前 plan + attempts / CAS proof] --> ORCH
  VERIFY --> SQL
  STATE --> STOP[应用 stop 归约 / receipt + terminal + safe-state]
  STOP --> ORCH
  STOP --> CTRL
  ORCH --> SQL[应用事务 / 完整 checkpoint / authority journal]
  CTRL --> SQL
  RT --> SQL
  STATE --> SQL
  SQL -. 显式 HA profile .-> HA[静态三成员 quorum / epoch / materialization]
  NODE <--> ART[Artifact CAS / 内容授权]
  NODE --> MEM[配置固定的 Local Memory provider]
  MEM --> QUERY[原实例 GET query / 实际 bytes 与原始日志]
  QUERY --> PROTO
  Entry --> MINT[持久 Memory intent / 对账 generation 与预算]
  MINT --> SQL
  MINT --> PROTO
  CTRL --> MAUTH[Memory tenant / selector / provider / 成员 generation]
  Entry --> MAUTH
  MAUTH --> MINT
  MAUTH --> ART
  RT --> PREP[原 Memory context / Prepare ACK / Group pending barrier]
  MAUTH --> PREP
  CAT --> PREP
  PREP --> SQL
  PREP --> PROTO
  MEM --> AVAIL[原 backend / 当前 bytes / 一次性 Probe]
  AVAIL --> PROTO
  PROTO --> PREP
  PREP --> PERMIT[Memory permit / 最早原期限 / 每次发送重查整组]
  PERMIT --> RT
  PREP --> REVOKE[durable Revoke ACK / 独立 stop hold]
  REVOKE --> PROTO
  ART --> UPLOAD[原 upload / bytes 校验与 fsync / CAS 归属 receipt]
  UPLOAD --> SQL
  ART --> CAT[Memory / Spatial catalog 与 placement evidence]
  CAT --> SQL
  FILES[来源固定的 PLY / PCD / planar occupancy 文件] --> SP[独立 CPU Spatial file provider]
  SP --> BUNDLE[不可变观测点 tile bundle / parent lineage / 计算证据]
  SP <-->|私有 Unix HTTP / 原 attempt 查询| NODE
  BUNDLE -->|Node 冻结并上传原 bytes| ART
  CAT --> TYPED[完整原 map / provider-qualified 字节副本]
  TYPED --> PREP
  TYPED --> SQL
  PREP --> USE[原 context 的当前使用租约 / generation / provider ACK]
  MAUTH --> USE
  USE --> SQL
  USE --> PROTO
  USE --> PERMIT
  MEM --> ULOCAL[同 kernel 与 time namespace / 私有 Unix / 每块读取复查]
  ULOCAL --> PROTO
  USE --> REVOKE
  REVOKE --> RELEASE[按 Task 或整组核对全部原 context 与 provider ACK]
  STOP -->|按需核对独立停止证据| RELEASE
  RELEASE --> ORCH
```

Controller 中 Orchestration、Control、Runtime、State 和 Integration 当前共址，责任边界由
Rust 模块及 transport-neutral ports 保持。Mission Service 是独立进程，Local EAIOS 位于
部署侧。默认监听 loopback；非 loopback 入口必须启用对应 mTLS 和证书权限 profile。
默认单 Controller，显式静态三成员 HA profile 使用独立副本 SQL 和 Controller 子进程。
动态成员、Federation、运行期重规划、完整时空/quota 和分布式 Spatial 消费属于本轮必需目标，
目前仍需按阶段实现和验收。

实际 Spatial 文件计算已接入 Node Service 的正式 Local How、prepared-artifact 和 CAS
发布链路。独立 verifier 检查来源文件与实际 tile bytes；Controller/Node 重启不再启动 worker。
首次进程记录使用显式 trusted-loopback profile；下述后续记录单独验证 mTLS producer 门禁。

当前 F4 producer 合同：typed-map v0.2 将原 producer provider/content schema 固定进不可变 manifest，
原 build provenance 与当前 publication attempt 分开。Node config v0.9 / Contract v0.8 必须显式
协商；catalog 在实际 writer gate 内核对当前证书、Node session、provider 和 Control/Runtime
绑定。旧 v0.1 不补 provider，远程 data-plane 不以历史兼容记录授权新发布。
Runtime 在实际 dispatch preparation 冻结原 Node contract、session 和 provider 声明；重试/恢复
不从新注册补旧证据。来源改变时 pending Execute 保留所有权并等待显式对账。模型参数不携带
这份内部注册证据，不能用来选择 provider 或 Node。

内容策略 v0.2 另外固定原 typed-map 全部 metadata 的 canonical digest；Control 重新构造投影
并同时核对通用与完整 Spatial 两份 digest。通用读取/import 不能丢弃 typed provenance；
旧策略缺依据时拒绝 map 读取，只有显式收紧升级可以增加 binding，已有 binding 不可替换或
移除。wrapper v34 拒绝旧 Controller 忽略新策略，包括无事件的策略事务。

副作用只在应用事务持久化并取得当前 quorum/epoch 后发出。副本只复制元数据和命令证据，
CAS 内容及 Local EAIOS 执行连续性需要独立恢复合同。Unknown 保留所有权；本地安全仍由
Local EAIOS 掌握。SoftwareProcess、测试合成观察和设备观察的证据等级不能混用。

当前 Memory 释放按实际范围检查：正常 Task 不等待仍在运行的兄弟 Task；取消、最终失败、
负向 verifier 与恢复保留整组或原停止 attempt 的 context/provider 门禁。回执固定原 backend
instance/contract，不能只匹配 provider 名称。Controller 与 supervisor 共享 wrapper v38；
旧 v37 读取后只作保守恢复，不刷新原预算。实际两 Node 文件路径、原子持久化与证据边界见
[生命周期验收](../../development/acceptance/2026-10-19-f4-memory-group-lifecycle.md)。

当前停止软件门禁见 ADR-0102。完整数据记录可由
`GET /v1/executions/{execution_id}/safety-stop` 检查；这个查询不刷新 TTL 或 stop deadline。
身份与 tenant 权威的当前调用链见 [ADR-0103](../../decisions/0103-http-command-reconciliation-and-node-identity-lifecycle.md)
及[身份合同](../../development/identity-lifecycle-v0.1.md)；证书登记不会增加设备 inventory。
Operator transport witness 随真实 writer guard 到 Commit/rollback，等待期间的撤权在事务内复核。
[实际 mTLS 排队检查](../../development/acceptance/2026-10-15-f4-operator-transactions.md)
核对第二个 registry revision 未提交。其它存储阻塞和只读快照降级仍需 F10。

State 与 MI 的当前软件路径见 [ADR-0104](../../decisions/0104-state-consumers-belief-and-semantic-verifier.md)
及 [F3 进程验收](../../development/acceptance/2026-10-15-f3.md)。Belief 是命名 producer，Control
只消费显式策略；它不取代原始 State 或成为 Global Truth。持续 verifier 的失效进入 stop/hold，
不能跳过实际停止证据。模型返回字段与配置 identity 均保留，缺失不补成已证明。

Memory 连续性的当前切片见 [ADR-0105](../../decisions/0105-memory-reconciliation-authorization-and-spatial-provider.md)
及 [进程验收](../../development/acceptance/2026-10-15-f4-memory-continuity.md)。Node Contract v0.7 /
config v0.8 显式协商原实例只读查询；导入原始身份先持久化，Unknown 不允许换 ID 重做。
该连续性切片引入 wrapper v31 / codec v22，内容门禁引入 wrapper v32、inner v21 和
upload codec v23；当前 producer 切片升级 wrapper v33、map codec v24 与 Node journal v8。
v8 保留原 v7 table layout/readonly legacy views，并通过双版本 marker 拒绝旧 v7 writer。
[内容授权合同](../../../contracts/memory/authorization-v0.1/README.md)规定显式 policy、成员版本、
稳定证书主体、当前 tenant 与实际 bytes 归属。Catalog/上传副作用重新核对当前权限，blob 流
逐块检查；已有 CAS 哈希不能授权另建 manifest，旧成员版本不能因回到原 Node 而复活。
Imported 历史不能授予当前可用性租约，Memory 对账不能满足 Task。

当前 pre-Execute 切片使用 MissionPlan v0.9 语义需求、Node config v0.10 / Contract v0.9，
在实际 operation owner 下选择已注册 receiver。原上下文、source session、policy/membership、
manifest/raw map 与 import ID 先持久化；Prepare ACK 之后才能导入，原实例当前 bytes 的
一次性 observation 通过之后才放行 Group 中的 pending Execute。Node 再检查完整当前内容、
配置、原预算和撤销 fence。接收预算取实际 Linux boot/monotonic，并保守扣除观察精度；
Controller 保留自己的 receive/deadline，两者不跨主机比较。
Revoke 先入 journal 再 ACK，先到也保留 tombstone。下载在每个有界写块检查 receiver 原预算，
CAS 服务同时逐块核对当前授权；精确原导入的 manifest 查询使用其 Read 授权，不授予目录发现。
Controller restart 撤销未结束的原准备授权，不重置时限或重做导入。wrapper v35 / inner v22 /
Node journal v9 拒绝旧版本，包括零事件快照和 SQLite 版本头回退。
实际两软件节点的 SQL 中断→原 provider 只读查询→当前可用性→真实计算→独立验证链路见
[准备验收](../../development/acceptance/2026-10-15-f4-memory-preparation.md)。
完整 Group lifetime/handoff、typed-map 消费、active-map/GC 仍属 F4 必需工作。

地图字节导入复用 prepared Memory 链路。Artifact 在事务写锁内将通用副本请求绑定到
原 published Spatial manifest，写 `MapMemoryReplicaRecorded`，包含完整原地图和消费端
provider；回放同样核对原 publication，不创建第二份通用 manifest 权威。Memory API 分别
返回字节 `replicas` 与原地图 `map_replicas`。wrapper v36 / event codec v25 拒绝旧 writer；
字节导入不授予地图激活或当前定位，完整语义消费仍在实施。

当前独立 Spatial 文件计算已通过[实际文件验收](../../development/acceptance/2026-10-15-f4-spatial-files.md)。
这些计算只生成文件产物，不能证明当前定位。实际 Node workflow 已冻结并上传原 bundle 至 CAS，
独立 source/tile verifier、mTLS producer 校验与旧二进制拒绝见
[producer 验收](../../development/acceptance/2026-10-15-f4-map-provider-authority.md)。
显式 Occupancy profile 使用 SE(2)，free/occupied/conflict/unknown 保持分离；原始 monotonic
deadline 不因 wall rollback 或换 host 续期。完整地图消费/激活、Group lifetime/handoff 与 GC 仍需完成。

### 原撤销的独立只读恢复

```mermaid
flowchart LR
    A[Authenticated recovery operator] --> B[Application query journal and original budgets]
    B --> C[Negotiated Node Contract v0.11]
    C --> D[Original provider GET only]
    D --> E[Append immutable observation evidence]
    E --> F[All original providers and context ACKs]
    F --> G[Independent stop and scoped Control release gates]
```

原始 Unknown 不覆盖，副作用不重复，观察期限不延长使用许可。Controller/supervisor wrapper
v39 与 Node journal v11 拒绝旧 writer。证据缺失或预算耗尽保留 ownership。
