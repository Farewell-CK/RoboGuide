# ADR-0247：原 Scene 地标进入 RG 与 MI

状态：实施与现场无运动接线验收。日期：2026-11-11。

## 决定

在原 Connector 的传感器观察工作器内，固定读取本机 Scene `127.0.0.1:50107` 的
state→regions→state，三次 GET 共用五秒预算。前后原地图绑定须一致，单响应最多2MiB，
World 正文最多48KiB，最多64个区域，每区域3至256个有限坐标点。没有新进程、线程、
遥控操作或原生服务管理。底盘采样仍在独立的原工作器内。

地标发布为独立 World schema `roboguide.world.scene-targets/v0.2`，原 Node 运行状态继续
保留。原 Scene 提供的 mode 空/null、generation null 原样保留，不从另一接口补造。
它们不验证定位、不可变地图版本、跨机坐标、原子地图快照、到达或运动权限。

## 数值与来源

第一版 v0.1 现场验收发现：原浮点数字经 Node/Controller JSON 链路后，两狗各九处坐标/
元数据改变到相邻的 f64 值，目标摘要因此不匹配。MI 及转换器拒绝该原证据；未降低摘要
检查或发起导航。两个端点的差异不能独立定位究竟哪个 Rust 解析/序列化步骤导致变值。

v0.2 使用 `definition_json` 作为唯一目标定义：它是 producer 的严格 canonical JSON
字符串，包含 physical_entity_id、source_node_id、原 map_binding 和排序后的原 regions。
`region_source_digest` 直接哈希该字符串的 UTF-8 原字节；消费者校验摘要并严格解析
该字符串，核对内外身份、重复字段、有限值、区域唯一性与几何边界。移除外层重复的
typed regions/map_binding，避免两份数字发生分歧。不能舍入、epsilon比较或跳过摘要。

Scene 原 HTTP body 摘要单独保存为来源声明；未传输原 body 的消费者不能声称重验了
那些 HTTP 原字节。稳定地标摘要不包含动态 pose 和接收时间，因此位姿或普通采样变化
不会不断改变目标身份。变更地标或绑定的原内容会改变身份。

## 时效与分工

缓存窗口15秒，从本次采样开始用本机 monotonic 计时；GET 只读缓存，不续鲜。采样失败
清除 current，过期明确 expired。MI 和 patrol_grounding 扣除 Controller 留存及本地读取
耗时；MI 冻结前还扣后续 Memory/语义/规划证据采集与校验耗时。禁止跨主机钟相减。

MI 只接纳精确 World 对象、`node:{source_node_id}/physical-observer` 来源、scene-targets
通道、原 epoch/sequence、未过期正文和原实体引用。每狗只从自己的来源选目标，不能
因为同名地图而借用另一狗地标。源码准备转换器不等于 Controller 动作准入。

独立到达验证源模板默认 disabled、未发布、未登记；原 plan digest、完整当前 attempts
与现场证据尚须生产接线。照片和汇总缺口继续存在。本次站立实验沿原 MI→RG→各自
Robonix，execution-report 与物理效果分开记录，不篡改旧任务或用新合同重解释旧历史。
