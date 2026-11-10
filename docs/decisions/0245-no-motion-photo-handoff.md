# ADR-0245：无运动的双节点照片交接

状态：实施中，现场验收独立记录。日期：2026-11-10。

## 决定

首轮真实双节点实验使用一个 Mission、两个 Actor 和两个 Task。
`capture-photo` 在狗2采集一张请求后的 RGB 图像并发布 Artifact；`inspect-photo`
依赖该 Task，在狗1读取原图并核对摘要、长度和容器尺寸。Context 使用既有
`sequential-handoff`，依赖就绪仍归 Mission DAG，Commit/Bind 和资源归原 Control。
不新增调度权威、独立服务或运动操作，不宣称 concurrent 或 tightly-coupled cooperation。

照片不能套用 Spatial map input binding。原 Connector 增加显式启用的 `photo-captures`
provider，只把部署预分配 selector 的本次原始拍照结果发布为 Global、exchangeable、
`artifact` Memory，内容 schema 为 `roboguide.rgb-image/v0.1`、media type 为 image/png。
Manifest 保留原 Mission、Task、attempt、原采集时间及不可变摘要；反复发现不更新它们。

狗1的 `image.inspect@v1` 是受限计算能力，复用原 Connector。其固定配置的只读身份
只能读取本租户 Controller 证据及本轮 Memory 内容。消费者核对本 Mission/Group 中
当前原 producer/consumer attempt、精确 camera State 来源和 Artifact finalize 回执，
再核对中央原 Memory manifest 与实际传输字节。拒绝其他任务的照片、歧义代际、过期
外层报告、取消/未知任务、摘要不符及调用者选择的路径或 URL。

消费者在原请求首次解析和下载前持久化 attempt/body；重复只返回原 handle/result，
重启在途工作保留 Unknown，不自动重做。图片处理仅验证 SHA、容器及声明尺寸，
不宣称像素解码、语义识别、视觉质量、定位或物理效果。

## 边界

本轮是受限 Global Memory 原图摄取，不是任意 Memory 的动态数据流绑定，也未验收
完整的 prepared-import、连续 use-lease 或跨 Group 手续。现有权限、租户隔离及内容
引用检查继续有效。新 handler 无权更改注册表、批准任务或操作底盘。

外层 State 的刷新只表示 provider 最近上报；照片是否本次产生由原 Task/attempt 和
采集边界证明。后续其他拍照覆盖 latest 时，本轮消费者拒绝，不能回退任意历史图。
这保留失败证据，暂不提供通用的多生产者队列。

## 验收

隔离软件验证先覆盖来源/任务/attempt/租户错配、缺发布、403、内容损坏、重复和
在途重启；再用真实两台 Node、中央 CAS 和摄像头完成一次原图交接。保存计划、
Commit/Bind、DAG 顺序、原 manifest、传输摘要和消费者回执。现场执行不能由离线测试
代替；Pilot journal 无新增记录分别确认不曾委派动作。
