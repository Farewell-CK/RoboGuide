# 狗2上电后的真实状态链路与采样阻塞修复

日期：2026-11-10，Asia/Shanghai。本轮只读取状态，不批准或下发运动。
本轮的动作边界来自用户上电前的约定；此前返程草稿仍未审批。

## 现场范围

ThinkPad 继续使用上一轮的实验恢复副本、R7 Controller 和 R9 MI，原现场库保留。
仅接触狗2；未连接或修复狗1，未重启 Robonix 或 Controller，未改 Node 配置。
狗2启动后的 Node 与统一 Connector 均在线。Controller ready、无 degraded、Unknown=0、
资源占用为空。原 Pilot journal 为 36 runs / 641 events，状态均为终态。

状态路径：Robonix 只读 chassis/state → 统一 Connector 的缓存 → 原 Node robot-state
export → Controller Node State。中央诊断仅 GET inventory 和 State，未创建 Pilot 查询任务。

首次有效报告：姿态 down、电量约97%，Mapping 标签为 `1101_visual_dog2`、localization
模式。这里的姿态是 Robonix 聚合报告；没有单独姿态测量时间、阻尼或遥控器连接状态。
Mapping 仅证明较新的 HTTP 响应，其位置测量时间、定位质量、不可变地图版本仍未知。

## 发现并修复的采样阻塞

修复前连续7次 RG 读取，3次可给出有效姿态，4次因嵌套 chassis 报告过期拒绝输出。
其中出现一次原 ROS 观察子进程8秒超时。外层 Node TTL 尚有效时，内部5秒窗口已过期；
这不是离线报告被悄悄刷新，诊断正确拒绝了过期值。

原 Connector 将 Status.sample、chassis.sample 和3秒等待串行执行；传感器采集约数秒，
超时可达8秒，因而延迟底盘采样。修复 `1525995f` 在同一个 Connector 进程内独立采样：

- 原传感器线程每次采样后等待3秒。
- 底盘线程每次采样后等待1秒，同一 client 仍仅由一个采样线程使用。
- 退出先通知并 join 两个线程，再关闭数据源；保留原 TTL 和未知/过期拒绝逻辑。

没有新增服务，没有用延长有效期掩盖阻塞。上游自身耗时或中断仍可导致过期，不能据此
声称姿态始终可用。修复仅更新狗2的 Connector 入口和新的 observation_workers 模块，
原入口已备份；无活动任务后重启现有 `rg-dog2-connector.service`。

实际部署字节 SHA-256：

- connector.py：`bf7abc157b8bebecaf0c06cf11cdbc8f4c322b2c6f8c3fc5fa2f2afcb6000249`
- observation_workers.py：`915e8f94d2f4b1f4b14a27572a08d367af08c7bca1cd1307e1a291e6f5d8bc29`

## 软件检查

ThinkPad 独立 fixture 源码包 `45e2ee67633d49cc9f8d5559cfa58bce2d633d67e883a0b4e558790c6e5267ed`
经传输校验，Linux 全包 unittest 67项通过，17.851秒，不接触真机或正在运行的 Controller。
其中3项新增线程回归覆盖慢 sensor 隔离、等待可打断及 chassis 在途读取先完成再关闭连接。
修改的 Python 文件通过 Ruff、格式、函数文档检查；新 helper 和测试通过 strict mypy。
Windows 全包运行受缺 grpc 及既有 OriginStore 文件锁限制，未计为通过；Linux 结果独立保存。

## 修复后现场复测

从 Controller 连续读取23次，覆盖44.683秒；23次均通过原 inspect_posture，报告 down。
最晚电量报告90%，这是采样时报告，不代表阅读本文时的实时电量。
底层 chassis 采样标识有21个不同值，11个与同时在狗2读取的原 source 标识逐值匹配。
包含 Controller 缓存停留时间后的最大有效 chassis age 为3283.471毫秒；未修改5秒上限。
Mapping 报告也通过现有位置诊断，但其定位质量仍未被验证。

将最后一份真实 State 的 Controller 观察时间在离线副本上推进6秒，姿态诊断拒绝旧值。
这是复制数据的过期探针，没有停止现场数据源，不能记为本轮实际断源/重连验收。

最终 Controller ready=true、degraded=false、Unknown=0、allocations=[]。
狗2 Pilot journal 的36 runs / 641 events与本轮最初读取的原行摘要完全一致。
新的运动委派数为0；未声称观察过现场物理动作。该44秒窗口不能替代长期稳定性验收。

## MI 读取边界

用部署的 R9 HttpMissionGroundingReader、同一服务配置和证书、显式 local tenant
分别在修复前后执行只读 capture，
没有创建 MI Request 或调用模型。返回 state_evidence=0、gaps=[]；Controller World State
确实为空。原有 reader 固定只读 World，而 robot-state 是 Node/robot，不能把此结果
解释为 MI 已读取当前姿态或定位。

现有 ADR-0037、0234、0240 的边界继续有效：不把整份 Node 健康/资源/执行信息改名为
World 以绕过合同。后续应给经过筛选的物理状态定义独立版本合同、来源和物理实体绑定，
复用现有 Connector/Node export 并显式接纳 schema。模型规划快照不能替代执行前的新鲜度
检查；上一轮模型耗时已超过底盘5秒窗口。

## 证据位置

Windows：`C:/workspace/DEAIOS/lab/rg-data-governance-20261110/dog2-poweron-state-20261110T161033/`。
ThinkPad fixture：`~/rg-lab/data-governance-20261110/dog2-poweron-state-20261110T161033/`。
狗2部署回执与原入口备份：`~/rg-lab/state-sampling-fix-20261110/`。
原始 State/source 采样、MI capture、软件日志和部署摘要均保留，不包含凭据。
