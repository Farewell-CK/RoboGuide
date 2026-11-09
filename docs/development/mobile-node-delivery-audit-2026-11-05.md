# 2026-11-05 Android 手机 Node 交付复核

本轮审核 AGY 提供的交付报告、Android 分支源码、桌面 APK、现有测试和 RG 生产协议。
结论为**不通过正式手机 Node 与真实采图闭环验收**。本轮确有原生接入代码增量，
但报告中的注册、真实采图、异常恢复及端到端通过结论超出可验证事实。

本轮没有修改 Android 业务源码、安装到手机或触发机器狗动作。隔离探针只连接本机
loopback，现场 Controller 仅查询 inventory，没有注册测试节点或改变已有任务占用。

## 检查基线与确实完成的部分

Android 仓库位于
`C:\Users\33457\Desktop\桌面文件夹\workspace\DEAIOS\roboguide-mobile-hub`，
分支 `feat/formal-node-integration`，提交
`41b257ad9e6398751b4891fc0f392dba76cc1ade`，业务源码工作区保持干净。

桌面 APK 为 `C:\Users\33457\Desktop\RoboGuide_Phone_Node_v0.4.apk`，
实际大小 **7,566,799 字节**，SHA256 为
`ffea13530de51b36916959206aed67f674142a7981051595df53d5647dc3c1f5`。
摘要与报告相符，报告的 9,883,878 字节不符。aapt/apksigner 确认仍为 versionCode 1、
versionName 1.0、Android Debug 签名。旧 APK 保留。

实际增加了 protobuf/gRPC 双向流、原生 Capacitor 插件、SQLite Node 身份与执行表。
真实模式移除了旧静态设备列表，开始请求 `/v1/inventory`，急停提示不再声称扭矩已切断。
这些是已完成的代码增量，尚不能证明注册和执行链路可用。

## P0：采图成功实际是程序绘图

`CameraCaptureHelper.java:38–134` 检查 CAMERA 权限后，始终执行
Bitmap.createBitmap、Canvas 画线/文字、JPEG 编码和哈希。
没有 Camera2、CameraX 或其他物理相机调用，也没有前台真实采图分支。
图片中还写入 `Device Sensor: Android Camera2 Headless Capture`。

这不是“缺少摄像头时的备用实现”，当前所有成功路径都是合成图。
`PhoneNodeManager.java:588–611` 随后将其作为 observation.acquire 的 COMPLETED 返回。
真实 JPEG 字节和正确 SHA256 只能证明文件存在，不能证明观察了现场，也不能满足
公共 observation.acquire 所要求的语义条件证据。

应先撤销该路径的真实采图成功语义。合成图只能用于明确的测试/演示合同。
真实采图必须来自物理相机；无法采集时返回有归属的不可用或失败。
若只实现原始照片采集，应选择语义相符的合同，不能擅自将“画一张图”或“拍一张图”
都视为 observation.acquire 已满足其 expected 条件。

## P0：注册声明不能通过当前 RG 生产协议校验

`PhoneNodeManager.java:337–380` 将 observation 能力 kind 和相机资源 kind 都写为
`sensing`。RG CapabilityKind 仅接受 mobility、transport、compute、observation，
ResourceKind 接受 space、compute、time。

本轮用**未修改的 RG Integration 生产服务**建立隔离 gRPC loopback，并从 Android 源码
重建注册字段，实际完成 Hello/Welcome 后发送三组注册。

| 输入 | 实际协议结果 |
| --- | --- |
| 原 Android 字段：能力 sensing、资源 sensing | InvalidArgument：capability profiles require unique contracts, known kinds, and one owner |
| 仅在探针中修正能力为 observation，资源仍 sensing | InvalidArgument：resource IDs must be unique with positive capacity |
| 探针对照：能力 observation、资源 space | 收到 Registered |

后两组只是拒绝原因的对照，不是对 Android 的修复，也不是手机或完整 Controller 验收。
当前原始声明会使整个 Node 注册失败，连同一个 Node 上的 ping 能力也无法注册。
相机能力始终 ready=true，也没有根据权限或真实采集可用性更新 readiness。

## P1：APK 的采图任务格式和 inventory 解析不匹配 RG

本轮将 `MissionCommander.vue:291–371` 的原始 submitMissionPlan 函数在独立请求 sink
中执行，截取它实际构造的 JSON，**没有发送 POST**。再调用 RG 生产
`orchestration::decode_mission_plan`。

- ping Mission 可解码。这个结果仅说明格式可解码，不说明执行或手机选择已通过。
- 采图 Mission 在 capability=`sensing` 处被拒绝，报 unknown variant，预期值包含
  observation。该请求还使用不存在的 resource_kind=`sensor`，修正前一项仍需修正它。
- 请求没有绑定实际 phone Node ID，未来多个相同能力参与者存在时，还需按 RG 现有
  实体/Actor 选择机制验证“指定当前手机”的含义。

`telemetryService.ts:285–306` 读取 `node.status` 和 `node.capability_profiles`。
实际 `/v1/inventory` 提供 liveness、reported_health、capabilities、contracts 和 resources，
不是报告展示的 status/capability_profiles 结构。
本轮用符合当前 inventory 合同的 Reachable/Online 样本执行原映射函数，结果为
OFFLINE、capabilities=[]，位姿还被填成 (0,0,0)。
现场 ThinkPad 的只读 inventory 也确认当前响应字段形状；当时只有狗2历史节点，
没有手机。临时软件环境的历史注册是否发生不能由此单独判定，仍需要原始证据。

## P1：持久受理、取消与恢复不满足报告承诺

`PhoneNodeManager.java:465–510` 在任何 journal 写入前就发送 COMMAND_PERSISTED，
随后才将任务排入 scheduler，`recordNewExecution` 在 544 行执行。
因此 ACK 与实际持久化之间存在进程中断窗口，不能称为“已持久化受理”。

`handleCancel` 在 621–625 行只更新数据库并发送 CANCELLED，没有中断/等待工作线程、
取消令牌、任务 Future 或未派发取消 tombstone。ping 和采图的后台成功路径仍能继续
写入 COMPLETED。对已完成执行也没有终态保护，收到 Cancel 可以覆盖为 CANCELLED。
这些是源码路径发现，尚未在 Android 真机重放，不应称为取消故障测试已通过。

同 execution_id 改变参数时，代码发送原 execution 的 FAILED 事件，不能将冲突请求
与原任务真实失败混同。冲突比较仅覆盖 operation 与字符串化参数，没有冻结完整目标、
预算、会话和执行授权。execSessionId 读取后没有校验，worker 使用可变化的全局 sessionId。

journal 保留了 SQLite 记录，但没有启动时未决任务归类、恢复 worker 或主动 snapshot
对账实现。重启后的 STARTED 在重复 Execute 时仅重新报告 STARTED，不能据此证明
任务已恢复执行或已经结束。事件 ACK 与可靠重发也没有形成完整持久流程。

应先建立真实持久接纳边界、原身份校验、取消状态机和终态单调规则，再验证重传、
冲突、响应丢失、取消与重启。不能用“存在数据库”代替这些生命周期证据。

## P1：Artifact 服务端缺口判断错误

报告称 RG 没有普通 Node 二进制上传接口，并建议另建 PUT 或 NodeSession ArtifactChunk。
现有代码已经提供独立的 Artifact HTTP 数据面。

- POST /v1/artifact-uploads
- POST/PUT /v1/artifact-uploads/{id}/content
- POST /v1/artifact-uploads/{id}/finalize
- GET /v1/artifact-uploads/{id}
- GET /v1/artifacts/{digest}

来源为 `apps/integration-server/src/artifact_http/server.rs:427–650`，
Node 参考客户端为 `core/node-service/src/artifact/client.rs:549–673`。
创建上传、分块、原 offset 查询、finalize、mTLS 与授权检查均已有 owner。
具体手机输出的合同、授权和发布关联仍须冻结并实现，不需要先发明另一套上传协议。

当前手机只将本地私有路径、摘要和大小放在 reason 字符串中，没有上传、远端取回、
原产物合同或发布确认。本地路径对其他节点不可用，不能算 Artifact 闭环完成。

## P1：UUID 身份没有替代实际认证

`PhoneNodeManager.java:170–174` 只使用 usePlaintext，没有客户端证书、服务端 CA
和节点身份绑定配置。固定 v0.4/v0.6 的 Hello 协商代码存在，但不提供认证。
当前实机基线要求的 mTLS 不能由这个实现满足。软件 loopback 测试可明确采用可信
本机模式；正式手机网络接入需单独实现和验证认证，不能将 UUID 持久化称为认证完成。

## 测试、报告与打包结果的实际含义

本轮独立重跑验证如下。

| 检查 | 结果与边界 |
| --- | --- |
| vue-tsc --noEmit、Vite build | 通过；独立输出目录重建的 HTML/JS/CSS 与 APK 内资源逐字节一致 |
| Android Java 编译与 JUnit | 临时 ASCII 路径下重跑 8 项通过；原中文路径重跑出现 ClassNotFoundException，路径问题保留 |
| 8 项测试内容 | 7 项是 protobuf 构造/序列化、常量、字符串比较及摘要格式，另 1 项是模板算术测试 |
| 生产 RG gRPC 注册探针 | 原字段被拒绝；仅修正能力仍被资源类型拒绝；修正两类枚举的对照可注册 |
| 生产 RG Mission 解码探针 | ping 格式可解码，原采图格式明确拒绝 |
| 原 inventory 映射函数 | 合同形状的健康样本被映射为 OFFLINE 且能力为空 |
| Android 真机 | adb 当前无设备，未安装、未运行、未采集物理照片 |

PhoneNodeVerificationTest 不实例化 PhoneNodeManager 或 SQLite journal，不建立 gRPC
连接，不调用真实采图/取消，不启动 Controller，不测试权限拒绝或进程重启。
幂等测试只是比较本地测试字符串，持久化测试只是序列化 COMMAND_PERSISTED 枚举。
`real-node-smoke` 是独立 Rust 软件参与者，即使其测试通过也不覆盖 Android 实现。
现有测试全部通过与以上真实拒绝可以同时成立。

报告中的库存 JSON、execution_id 和图片摘要没有对应的原始 HTTP/Node 流、完整图片、
运行目录或 Mission 结果证据。库存 JSON 的结构还不符合当前生产视图，不能作为已验证
的 Controller 实际响应。应将示例明确标记为示例，撤回当前“端到端全部通过”的结论。

另有生成代码来源问题。RoboguideNode.java 头部声明 protoc 4.31.1，但运行期生成版本
校验参数被手改为 4.29.3，Generated 注解也被注释。没有一并提交原 proto、固定生成
命令与来源摘要。应采用匹配的工具链重新生成，不能以修改校验常量代替版本一致性。

原始输出、APK/源码摘要、JUnit 日志、请求 JSON 和隔离探针保存在工作区
`lab/mobile-node-audit-20261105/`。所有探针的范围独立标明，没有将软件样本视为真实手机。

## 给 AGY 的下一轮修复顺序

1. 撤回“正式手机 Node 与真实采图闭环全部通过”的交付表述，列出已编码、软件验证、
   待联调和待真机各项事实，库存/回执示例不作为运行证据。
2. 修复能力、资源、Mission 和 inventory 字段，接通受认证 NodeSession。
   先让原 Android 客户端在隔离真实 Controller 注册，并完成 ping 的 Mission/attempt 闭环。
3. 修复先持久化再回执、完整输入冲突、原 session/执行授权、取消确认和重启对账。
   补生产 Manager/journal 的真实边界测试，包含晚到成功不能覆盖取消、未知结果不重放。
4. 用物理相机实现采集，权限、前后台和采集失败如实处理。合成图退出真实路径，
   首个采图能力选择与实际效果一致的合同，不强占更强的语义观察成功。
5. 复用既有 Artifact HTTP 数据面，完成上传、finalize、远端取回和原任务产物关联。
6. 提供原 Controller/Node 流日志、实际任务和完整图片，再做重传、冲突、取消、断连、
   权限拒绝和进程重启。没有 Android 设备时保持相关项目未验证，不补造通过证据。

通过上述手机自身验收后，再推进[手机与机器狗协作设计](mobile-field-client-design-draft.md)。
机器狗原 SafetyStop 与人工对账问题仍独立保留，不随手机功能增加而解除。
