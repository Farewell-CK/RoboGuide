# ADR-0105：Memory 对账、分布式授权与空间 provider

状态：Accepted，实施中，2026-10-15。承接全部非发布计划 F4；完整实现与总验收前不能声明完成。

## 权威与协议

Control 保持 Group/membership/binding 与授权 generation 的唯一权威；Runtime 保持完整当前
attempt 及 Context 引用；Local EAIOS 保持 Memory 语义与 backend storage 权威。Artifact CAS
只保存不可变 bytes。scope、visibility、placement、tenant 和授权是不同维度；发现或持有
content digest 不授予内容访问。Group 成员与 handoff 变化必须变更显式 generation，不能依赖
客户端声明或把 Artifact reference 当作 node-local placement。

Node Protocol 采用显式协商扩展提供对账与授权证据。旧客户端可以继续使用原合同，未协商的
扩展命令不得发送；忽略未知 protobuf 字段不能成为新功能支持证明。扩展不承载 blob，也不
改变 Registered/Ack 必须对应应用持久 Commit 的规则。新功能保留完整 schema/版本 fence。

## Unknown 导入

原 import/command identity、manifest/selector、consumer provider、LocalSystem、工作流合同
identity、原授权上下文、原预算和 provider instance identity 在副作用前持久化。重启不重新
执行 import。Unknown 只允许读取原 provider 的显式 query 合同；不支持查询保持 unavailable，
不能用新 import ID、换 provider、重新下载/导入或补一条成功观察规避不确定性。

对账有自己的 command ID、期望 generation、非续期 deadline 和次数，必须先经 Controller
事务及当前 quorum/epoch 再发送。Node 先持久接纳，再调用配置固定的 readonly provider query。
结果绑定原完整 identity 与原 provider instance，并保留原查询 bytes digest/receive time。
`not found` 不能推断历史副作用从未发生；只有明确、持久且合同匹配的 completed 或
rejected-before-effect 结果可以解除 Unknown。迟到或 ownership 已变化的报告保留历史，
不能激活旧 Group、改变 Task satisfaction 或扩大内容权限。

## 内容授权与生命周期

同 tenant/Group 的当前成员资格、确切 consumer provider、Memory selector、操作和授权
revision 在 Controller/Node/Artifact 各边界核对。跨 Group 使用不可变的新 manifest、显式
exchange/handoff 授权和 parent lineage，不能原地改写原 scope。撤销先 fence 新访问与新工作，
保留已持有资源和原始证据；运行中的撤销按 stop/handoff 门禁处理，不直接删除正在使用的 bytes。

active-map 是版本化 selector/租约证据。发布、配准、融合或导入不等于当前设备已经定位，也
不直接替换运行中 Mission 的冻结 reference。切换必须检查消费者、import/localization 合同、
当前 attempt 与接受计划的 reference；不兼容当前执行先停留在候选，交给版本化安全切换。

删除先持久 tombstone；GC 取得全量当前 execution/import/active-map/parent/replica/
snapshot/archive 引用图及其 revision 后才操作 bytes。未知引用或不完整 restore/archive
profile 拒绝删除。物理回收有独立 command/receipt 与崩溃对账，不能先删内容再补授权。
删除、撤销与 tombstone 不删除审计事件或伪造远端 Local EAIOS 已清理。

## 空间计算

Spatial provider 与 Rust Control 分离，使用独立 Python 3.11 / Open3D 0.19.0 环境。输入是
具有来源、digest、单位、frame、预算和 revision 的实际 PLY/PCD 或版本化 occupancy 文件。
执行 FPFH/RANSAC → ICP、pose-graph、多源三维分块融合与不可变增量 revision；退化、低重叠、
预算耗尽及未知区域保持 unavailable/unknown。阈值和预算由配置给出，不按设备名或地图名分支。

输出保留输入/parent digest、transform、实际 residual/coverage、算法配置与依赖 identity，
通过 CAS 发布。配准质量指标不能换算成未经校准的 covariance，也不能生成当前 Node 的
localization proof。外部文件/离线几何验收与实际设备定位保持不同证据等级。

与 F5 的 snapshot/archive/CAS restore、F7 的 stop/handoff 和 F10 的全栈运行验收共同闭环，
不因阶段边界删除这些必需项。

当前 `integrations/spatial-provider/` 已实施实际 PLY/PCD CPU 文件计算；
[文件验收](../development/acceptance/2026-10-15-f4-spatial-files.md)通过独立变换真值、
三源回环、单位转换、不可变增量及退化/低重叠/损坏/超时检查。原始 source、代码、三个核心
native binary digest 与版本均保留；这些 binary digest 不声称覆盖完整传递依赖。
该文件切片不拥有 Node、地图激活或定位权威。后续显式 Occupancy profile 使用有界 SE(2)
correlative search、rigid ICP 和固定 anchor pose graph，不将平面输入假扮成三维观察。
free 要求完整输出 cell 被显式输入 free square 覆盖；occupied footprint overlap 和冲突保留，
未观测/intermediate 保持 unknown，parent conflict 不成为无歧义配准点。结构合同见
[file-provider schemas](../../contracts/spatial/file-provider-v0.1/README.md)。

两个文件 profile 共用原始 job intent/worker。实际 Linux CLOCK_MONOTONIC 与 boot identity
在 source snapshot 前冻结；父子进程共用原 deadline，wall rollback 不延长预算，换 host/reboot
不能重启旧 job。原始 completed proof 可在重启后只读核对，legacy wall-only 证据不补成新证明。
这些 clock 身份只是软件进程域，不能证明硬件身份。

私有 Unix HTTP Local How 以真实 backend UUID 和原 attempt admission 管理一次实际 worker，
不创建 Node inventory。部署生成器只输出配置，多个语义 slot 共用 canonical build/publish。
Node Service 冻结构建产物，再上传 CAS 和发布原 source provenance；Local How 不越权写 catalog。
取消接纳仍为 RUNNING，实际原 worker 退出后才形成软件级 CANCELLED；不能代替 Controller 的
独立 stop/safe-state 合同。[正式 Node/CAS 进程验收](../development/acceptance/2026-10-15-f4-spatial-node.md)
已通过显式 loopback compatibility 路径和独立 verifier，并验证重启无第二 worker。
远程 typed-map 的实际 provider/当前发布 attempt/session 仍须版本化接线；未据此关闭 F4。

当前 producer 切片以 typed-map v0.2 显式加入 provider 与内容 schema。完整原 build attempt/Task/
LocalSystem 必须保留；当前发布 attempt 和 Node session 仅作为请求 context，由当前证书和
Control/Runtime 重新查验，不是 header 授权。Node config v0.9 / Contract v0.8 显式协商这条
路径；Node journal v8、Controller wrapper v33 / event codec v24 拒绝旧软件写新权威。
旧 typed-map v0.1 保持原始读取语义，缺 provider 不能自动迁移成已注册的真实 provider；远程
data-plane 新发布必须走新合同。管理恢复若需要导入旧历史，必须另有来源绑定迁移。

注册依据在 dispatch preparation 时冻结进 Runtime command，保留实际原 Node Contract、
已接受 source session 和 provider 声明。它不进入 MI 语义参数。原 attempt 的 retry/restore
使用原快照，包括 legacy unavailable，不因新注册补新依据。发送前核对同 session/contract
及原 provider 声明；变化则保留 pending 和所有权，等待显式 reconciliation。发布时同时核对
原 build、当前 publication 两份 dispatch 声明与当前 provider，阻止新软件替旧工作补身份。

## 当前内容门禁切片

Control 的完整 Memory resource policy 与 Artifact certificate registry 通过全局 recovery-admin
管理 API 和原始 command receipt 持久化；租户管理员不能替换全部署权威。不可变 source/tenant、
撤销与完整原 policy history 在 restore 时复核。Group binding digest 相同也不能复活旧 generation；
旧快照缺少 membership 时不补新授权。Import intent 保留原接收 provider、tenant、policy、成员
版本和已认证提交者，重试不更新这些依据。此处仍不是完整 Node Group lease/ACK 合同。

Artifact 使用当前 Control 中的稳定证书主体及实际 TLS leaf/notAfter。写入前在现有 writer gate
内复核；blob 每块发送前复核。Uploaded bytes 在完整 hash/size/fsync 后才得到 Prepared 证据，
CAS 放置后形成 Completed 归属。最终 SQL 失败可以对原 CAS identity 做只读核对；Started 不
证明内容曾完整写入。既有 CAS 哈希、另一个 selector、typed-map alias 或 read grant 均不能
替代来源归属或显式 scope exchange。当前没有完成的 handoff 路径继续拒绝。

本切片使用 wrapper v32、inner v21、upload codec v23，并保留旧 v31 连续性记录；Node journal
v7 不变。上传归属事件在完整 snapshot/archive/replay 合同落地前不能 GC。原始工作流 context
保持不变；Node 从既有 continuity identity 读取 opaque import purpose，通过当前 Controller
查验其实际权限。该 purpose 不是 bearer credential，也不证明 Local EAIOS 已停止或执行完成。

Operator 请求同样携带显式 transport witness 到真实应用 writer gate。取得锁后重新核对
actual leaf/notAfter、部署文件与已提交 registry、稳定 principal、role、tenant 和当前 Mission
归属，再向应用事务提供同一 mutex guard。排队期间的撤权不能沿旧字符串主体执行；内部
startup/timer/Node 输入仍走同一真实 writer。Registry SQL 使用 Tokio 的同步事务 worker handoff，
使真实排队检查可推进；这不声称全部存储阻塞或 degraded-read 问题已经关闭。见
[实际排队撤权验收](../development/acceptance/2026-10-15-f4-operator-transactions.md)。

## 完整 Spatial 内容策略

Memory authorization v0.2 在通用 manifest digest 之外，显式固定原始 typed-map manifest 的
完整 canonical JSON SHA-256，包括 frame、anchor、format、resolution、parent 和全部来源。
Control 从原 map 自行构造通用投影，不接受调用者提供的替代投影。需要 typed-map 依据的
策略不能由通用 Memory 路径读取或导入；后续 import 协议必须保留这份完整来源。

v0.1 保持可恢复但不能包含新字段。管理者可用期望 revision 将历史策略显式收紧为绑定完整
map 的 v0.2；不允许修改既有 digest、删除 typed digest 或降回 v0.1。缺失依据绝不从当前
元数据静默补齐。wrapper v34 防止旧 Controller 忽略这条新权威，包括零事件策略更新。
这是内容策略切片；Group grant、pre-Execute、可用性、handoff、active-map 和 GC 仍需闭环。

## 执行前准备与当前可用性（实施中）

MissionPlan v0.9 的 Role requirements 可包含至多 8 个 semantic Memory selector、kind、
payload schema 和 media；不含 consumer provider、Node、路径或当前 inventory。旧 v0.8
及以前不能携带该字段。Control 在实际 binding 后根据当前注册 provider 选定精确消费者，
验证内容策略与当前 Group membership，冻结完整原 manifest/typed-map 与 import identity。

新 Node Contract v0.9 显式协商 Prepare / Probe / Revoke。Prepare 持久化原调用、provider
声明、策略/membership、接收 session 和不可续期预算，只有 durable receipt 后允许 import；
它不执行 Local How、不创建所有权、不证明内容可用。所有输入实际导入且原 backend 的只读
availability 检查通过后，Controller 才发送带 exact preparation 的 Execute。Node 在调用
Local How 前复核原 context、预算、当前 provider、完整 bytes 和 backend instance。

Node 原始预算使用同 host boot 的 monotonic receive stamp；重复消息、连接/进程重启不续期，
不同 host boot 拒绝。Controller 使用自己的 receive/deadline，不能比较两个主机的 wall clock。
撤销先 durable fence 后 ACK；失联、无 ACK 或 backend 不可用保持未知与所有权，不用 Imported
历史充当当前可用性。反复 probe 也有原始次数/时间预算，不允许无界缓存 lease。

完整 raw typed-map 随 import context 保留；通用投影不能丢弃 frame/anchor 来源。wire 只传
metadata 与 identity，bytes 仍走 CAS。准备权威以 wrapper v35 / inner v22 和 Node journal v9
持久化，旧二进制拒绝；原有事件 payload 结构未变，保留 codec v24。命令/回执原文与完整
checkpoint digest 仍在应用 authority journal 中提交，事件日志不取代该权威。
所有正向证据使用真实 CPU 文件 provider 与正式 Node，不构造机器人、传感器或物理停止事实。

当前文件进程切片已验证 Prepare ACK、原导入中断只读查询、当前 bytes Probe 和整组 pending
barrier。精确原 import 的 manifest/副本历史 GET 使用其 Read 权限，不授予目录发现；重查有
原始预算和退避，重复 receipt 不更新时刻。Controller 重启在开放监听与恢复 outbox 前持久
撤销原准备授权。当前 120 秒是原准备/导入/Execute 接纳窗口，尚不是可续约的运行中 Group
内容租约。完整 Group lifetime/handoff、typed-map 消费、active-map/GC 继续实施，不由本切片关闭。
