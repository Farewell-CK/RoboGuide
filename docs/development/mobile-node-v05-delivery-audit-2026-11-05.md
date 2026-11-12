# 2026-11-05 手机 Node v0.5 交付复核

本轮审核 Android 提交 `18f1372cf0638809a9973e592264de58c66a9d34` 及 AGY 的新报告。
结论为**软件协议联调取得实证进展，Android 真机与真实采图闭环仍未通过验收**。
注册字段错误已修复，不能再沿用上一轮“原注册必然失败”的结论；当前主要问题是
将 Rust 软件参与者的结果当成 Android 实现与物理相机的验证，以及若干未修复的生命周期缺陷。

## 基线与检查范围

- Android 仓库分支为 `feat/formal-node-integration`，当前业务源码干净。
- APK 为 `C:\Users\33457\Desktop\RoboGuide_Phone_Node_v0.5.apk`，7,646,756 字节，
  SHA256 为 `1a8af4f3e785024fb16e4044fcce2c4af317ea499d0819e7d1a979b13d79ef1b`，
  与 AGY 报告一致。原 APK SHA256 仍为 `84a106dd...a77213`，基线未变。
- 本轮只审查 Android 代码与 APK、运行单元测试、隔离软件探针。adb 未发现设备，
  没有安装或执行 APK，也没有连接物理相机、发送机器狗指令或处理原安全占用。
- RG 主仓库中的 `verify_phone_node.rs`、real-node-smoke 的 sha2 依赖及 Cargo.lock
  是 AGY 已留在工作区的未提交增量。本轮保留，未把这些变更混入审核文档提交。

## 已修复与新增的真实代码

1. Android 注册的 observation 能力 kind 已改为 observation，相机资源改为 compute。
   前端采图 Mission 同样修正了 capability/resource_kind。
2. 使用当前 APK 的原 submitMissionPlan 函数截获请求、调用生产 RG decoder，
   ping 与采图两个 Mission 均可解码。本检查没有发送 Mission POST。
3. CameraCaptureHelper 新增了 Camera2、ImageReader 和异步 capture 路径。
   这证明采集实现已增加，不证明在具体 Android 设备上工作。
4. 新增 Artifact 创建/content/finalize 请求，以及 ExecutionSnapshot 回放代码。
5. 原生 9 项 PhoneNodeVerificationTest 加 1 项模板测试在临时 ASCII 路径下独立重跑通过。
   前端 vue-tsc --noEmit 通过。测试范围仍以序列化、常量、测试字符串和摘要格式为主。

## 独立重跑软件探针的结果

本轮从当前源码构建 integration-server 和 AGY 的 verify_phone_node，在全新临时数据根
`/tmp/rg-v05-audit-qzw2rb1n` 启动仅 loopback 可访问的 Controller。
端口采用探针固定的 39151/39152/39153，事前验证无监听占用。运行结束只停止本轮创建
的 Controller。原始日志、二进制摘要和独立 HTTP 视图保存于
`lab/mobile-node-v05-audit-20261105/`。

探针退出码为 0，独立查询确认如下。

| 观察 | 实际结果 |
| --- | --- |
| 软件 Node | 完成 Hello/Register/Heartbeat，使用 node-phone 名称 |
| ping Mission | mission-ping-1793847263256847238，Completed |
| observation Mission | mission-obs-1793847263256847238，Completed |
| Artifact | 创建、写入、finalize、GET 回读测试字节成功 |
| Snapshot | 重连后两项已完成任务的 Snapshot 得到 ACK |
| 资源投影 | 收尾 allocations 为空 |

因此这次软件链路和 CAS 存储确实有运行证据，不能将全部结果简单称为“只有模拟界面”。
但以下边界决定了它不能作为 Android 真机验收。

### P0：Rust 探针代替了 APK，并使用固定测试字节

`apps/real-node-smoke/src/bin/verify_phone_node.rs` 自己建立 gRPC、注册 Node、接收 Execute，
然后自己发送 ACCEPTED/STARTED/COMPLETED。它不加载 PhoneNodeManager、SQLite、Camera2
或 APK，也不调用 Android 端上传函数。

其 345 行定义的上传内容为：

```text
RIFF_FAKE_JPEG_OPTICAL_CAPTURE_DATA_FOR_PHONE_NODE_TEST_1234567890
```

长度为 66 字节，SHA256 恰为报告中的
`c54f890ba67517fd5d2705a426ff2f076406e548a2f6d166cf63435f26b6d230`。
它没有 JPEG 文件签名，也不是照片。CAS 正确保存任意字节，不负责把这些字节变为现场观察。

探针的通过断言检查了协议 ACK 和字节回读，没有检查物理相机、Android 持久化、取消、
进程重启或 mTLS，也未在原探针里查询 Mission 终态和资源释放。本轮独立补查了后两项。
重连两项已完成的软件任务不等于杀死 Android 后恢复未决工作。

### P0：真实相机失败会静默退回绘图，取消也可能进入同一路径

`CameraCaptureHelper.java:82–109` 中，tryPhysicalCameraCapture 返回 false 后，
无条件调用 generateSensorFallbackImage，随后 success=true。
没有测试专用开关、DEMO 条件或合成来源标记。
相机不支持、被占用、配置失败、超时等生产错误同样可能进入该路径。

Camera2 的等待在 228 行，233 行 catch Throwable 也会吞掉线程中断；随后返回 false，
外层继续生成合成图。照片上还写入 `Android Camera Sensor Capture`。
这与报告“任何环境稳定测试”的说法不能混为真实采图成功。
真实任务失败时应保留真实失败或未决结果，合成内容必须退出真实任务合同。

CameraDevice、CameraCaptureSession 与 ImageReader 在成功/超时的路径也没有完整关闭，
仅退出 HandlerThread。物理采集的连续执行、取消及资源释放仍需真机测试和修复。

### P1：持久化确认仍早于落盘，并新增并发重复执行窗口

`PhoneNodeManager.java:502–514` 仍先发送 COMMAND_PERSISTED，
588 行才 recordNewExecution。进程在两者之间退出仍可能丢掉已确认的命令。
取消入口在 751–762 行也先声明持久化，之后才更新任务状态，没有 durable cancel intent。

本轮 scheduler 从单线程改成 4 线程，但 checkConflict 与 recordNewExecution 仍是两个
独立调用，未在一个事务中完成 admission。两个同 execution_id 的 worker 可能先后都
查到不存在，然后分别开始执行，CONFLICT_REPLACE 也可能覆盖原记录。
这条可达并发交错不能由测试中的字符串相等断言排除。

应把原命令、完整输入与状态持久接纳作为原子边界，再发持久回执；重复和冲突必须
作用于原记录，不能靠只比较 operation/参数字符串代替完整授权与身份一致性。

### P1：Future.cancel(true) 不构成最终取消证据

746–772 行对 Future 调用 cancel(true) 后立即写 CANCELLED，没有等 worker 及相机/
上传资源结束。任务成功路径没有防止后续 COMPLETED 覆盖取消的终态门禁。
ping 被中断后也会进入 catch 写 FAILED，不能保证原 CANCELLED 单调保留。

对尚未 admission 的取消没有 tombstone，future tracker 的 put/remove 与实际执行
启动也不是原子状态机。重传 worker 会覆盖 map 中同 ID 的 Future。
已结束任务仍可被无条件改写为 CANCELLED。
这些是源码可达风险，本轮未在 Android 真机注入，不把它们描述为已发生的物理故障。

### P1：上传失败仍无条件宣告任务成功

`PhoneNodeManager.java:640–659` 将 uploadArtifactToController 的结果放入
uploaded_to_controller，但无论 false/true，都写 COMPLETED 并上报 status=ok。
因此当前代码不能满足报告要求的“采图并上传产物闭环”成功条件。

上传身份每次临时生成 UUID，没有 durable upload intent、原 offset 查询、丢失回复
对账或重启续传。finalize 只检查 HTTP 状态，没有核对远端回执或回读。
这些机制没有被 Rust 客户端的独立请求实现所验证。
Artifact 端口还是私有字段固定 8090，APK 没有对接探针使用 39153 的配置入口。
图片的任务关联仅放在 reason JSON，正式输出合同与可消费的产物关联尚未验收。

### P1：Snapshot 回放未补齐未决恢复，认证和 inventory 缺陷仍在

replaySnapshots 仅遍历最后 50 条 SQLite 记录，把保存的 phase 发出去。
启动时未把中断工作归为未决，也没有恢复执行者/查询器。STARTED 可能继续只是历史状态。
sequenceGenerator 重启从 1 开始，未按持久事件序列恢复；session、执行授权、原预算
及完整目标的验证也没有补齐。不能把重放快照描述为重启恢复已完成。

Node 连接继续 usePlaintext，上传继续 HTTP，没有接入现场要求的 mTLS。
软件可信 loopback 测试不验证远端手机身份认证。

telemetryService 的 inventory 解析此次没有修改，仍读取 node.status 和
node.capability_profiles。本轮重跑原映射函数，符合实际合同的 Reachable/Online
样本依旧变成 OFFLINE，能力列表为空，未知位置仍被填成 (0,0,0)。

## 测试应怎样表述

“9 项 Android 单元测试通过”属实，但新增的 Snapshot 和 Cancel 用例仍只是构造
protobuf、序列化并断言字段，不运行 Manager/journal/worker 生命周期。
它们不能证明持久受理、取消、并发幂等、重启、相机或网络异常已通过。

报告应分别写为：

- 已编码：注册字段修复、Camera2 候选、上传候选、Snapshot 候选。
- 软件已验证：Rust 参与者经过 Controller 的注册、任务派发、回执、CAS 测试字节
  与已完成任务 Snapshot 重连。本轮独立确认两项 Mission 终态与资源释放。
- 尚未验收：实际 Android Node 接入、物理照片、Android 上传、权限/取消/断连/
  未决任务重启及现场 mTLS。原 APK 没有在本轮连接设备上执行。

## 下一轮建议

1. 禁止真实采图失败时合成成功；先验证 Android 上真实相机的一张完整照片。
2. 修复 durable admission、并发幂等、取消终态与未决恢复，再测试生产类而非只测消息字段。
3. 上传失败不得满足要求远端产物的任务，补原上传身份持久化和对账。
4. 用实际 APK 连接隔离 Controller，保存设备身份、原 Mission/attempt、SQLite、
   Logcat、物理图片、远端取回摘要和最终资源投影。手机未就绪时明确保留待验收。
5. 修复 inventory 和现场 mTLS，避免给出“填入 IP 即可正式接入”的未经验证承诺。

当前机器狗运动验收和原 SafetyStop 占用继续独立保留。只修改软件 probe 名称或通过
文案，不能替代手机与现场实验所需的实际证据。
