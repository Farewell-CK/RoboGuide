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
