# 时空资源声明与原预订合同 v0.1

本模块由 [ADR-0209](../decisions/0209-spatiotemporal-resource-authority.md) 定义。
Domain 提供明确的空间边界和本地时间窗口；Control 保有版本化部署策略、原 reservation 和
future calendar；Application 接收授权更新、提供部署 booking budget 并在超时后委托原
whole-Group stop / Memory revoke。Local EAIOS 继续拥有 Immediate How 和最终安全权威。

部署通过全局 recovery-admin 的 `/v1/resource-topology` 安装完整 registry。每项引用真实部署
声明的 Node/resource ID，安装策略不会注册 Node 或构造资源。Space 使用 conflict domain、
frame、anchor revision、整数毫米 AABB 与 clearance；同一 conflict domain 的参考必须一致。
两个不同 ID 的膨胀边界相交或接触也冲突。Time 使用明确的 `time_domain`，不同 ID 的别名仍
共享一个排他日历；可用窗口和 booking 都是半开区间。未知变换和远端时钟不能证明可用。

`booking_duration_ms` 是部署声明的 Task 预订和超时上限，不是模型预测。
`booking_max_age_ms` 以原 Mission acceptance 为锚点。Application 经现有 attributed duration
port 传递该预算，记录精确 registry revision，重试不会更新接受时间。原 canonical MissionPlan
不增加 Node、后端、资源 ID 或模型 duration 字段。已有其他 duration source 的 Core 调用仍受
明确部署 booking 上限约束。

Scheduler 使用不可变 Control snapshot，联合比较资源、空间冲突、时间域和可用窗口，并将
opening 纳入最早开始时间搜索。正常、Group 与恢复 Commit 在 quota、reservation、event 变化
前各自检查完整批次。Bind、Rebind、保留绑定 continuation 和每次 Execute outbox 投递都复检
原窗口。恢复只使用原 Task 尚未耗尽的 booking end；替换不会重写原 decision，当前 replacement
投影单独保存并与 Group bindings 核对。Context 资源保有原 parent horizon，各 Task 还受自己的
原 booking 约束。

原活动或 Unknown reservation 不因计划结束而释放。超时 timer 不依赖 Node 新输入，先进入
既有整组停止、Memory 撤权与 Mission hold。没有独立 safe-state 声明或证明时保留未确认/人工
处理状态，即便已经收到 Cancel 或 Local terminal。当前持有或 future entry 不能被策略更新
移动、删除或改写；不相关 entry 可按连续 revision 更新。

Controller wrapper v58 和 recovery profile v0.27 增加原策略、声明和窗口的持久校验；Node、
MissionPlan、Memory 与 event codec 保持原含义。恢复核对完整原 history、强制 reference tables、
当前原 reservation 和 future 冲突，保留全部既有 SQL/CAS/upgrade/Memory 恢复要求。恢复不启动
服务，不重新授予权限。旧 profile/writer 拒绝新 authority。

实际验收入口：

```bash
uv run python tools/quality/check_state_verifier_processes.py \
  --output /tmp/explicit-fresh-calendar-root --skip-build --resource-calendar \
  --previous-controller /explicit/genuine-v57-controller
```

该入口运行真实软件 CPU 子进程、原 Controller/Node SQL、独立文件 verifier 和实际 CLI。
原 stop 声明缺席的 overrun 是拒绝释放的负向验收。几何合同测试是明确标注的离线输入；当前
进程 gate 不生成物理空间观测或设备。跨 Controller/跨主机时钟转换、完整在线 HA 和整体验收
继续归 E、A1/A2、F；不能用同机进程记录宣布这些条件通过。

见[本模块验收](acceptance/2026-10-25-resource-topology.md)。
