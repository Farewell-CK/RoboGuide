# ADR-0233 本机管理员取消结案

状态：已实现并通过隔离软件验收，现场结案另行记录。

## 原因

狗2已执行起身计划，但 Pilot 没有给原任务终态。主机随后重启，新的原生进程没有旧
会话；适配器正确保留 UNKNOWN。仅有重新归约原生终态的工具不足以结束这种占用。
不能清空 journal、伪造原生事件或把已发出的动作声明为 no-effects。

## 决策

本机任务模块提供离线 `robonix_task.manual_settlement` 运维入口，不新增网络动作接口。
停止同一 Connector 后，通过原有 OS journal 锁取得唯一写入权。管理员必须明确给出
原 handle、请求摘要、序列、完整原行和原事件字节摘要、confirmed、stopped 结论及
核验记录。身份取自实际本机登录用户与 UID，不从命令文件接收。

只允许对已经请求取消并尝试 scoped abort 的 dispatched UNKNOWN 记录进行人工结案。
核验当前本机任务是否停止及现场设备状态由管理员负责，软件不把任意证据 JSON 当作
设备证明。原请求、会话、事件字节、原创建时间不变；新审计表保存明确管理员决定和
旧状态、旧详情、旧序列。相同主体的同一命令只返回原回执，冲突与陈旧输入拒绝。

本机状态变为 CANCELED，仅用于向 RG 回传取消终态。该记录永不导出 no-effects，
不写入 physical StopConfirmed，不授予自动恢复或交接。Controller 的原始取消和
ManualEscalation 独立保留，仍需其已有认证管理员结案；不能单靠本机运维决定释放
Controller 占用。后续新任务需要独立明确授权并重新经过正常 Control。

首次写入管理员决定时 journal profile 显式升为 `lab-pilot-journal/2`，使原 v1 writer
在恢复时拒绝。原 v1 数据仍可由新代码读取，不隐式升级。新代码恢复 v2 时完整校验
管理员回执、原身份和原事件摘要；缺失、篡改或游离标记均拒绝。不可通过删除新字段
或改回版本号进行降级；需保留新代码及完整原记录。

## 验证

隔离 journal 检查覆盖 OS 唯一锁、陈旧/跨任务命令、明确确认、幂等/冲突、冷恢复、
原生字节篡改拒绝和不产生 no-effects。实际前版模块须对新 journal 拒绝且不改原数据。
原 Controller/Node、真实 loopback 原生协议回归及现场动作结果分别记录，软件测试不
证明实际设备停止或站立。

检查结果见[软件验收](../development/acceptance/2026-11-05-local-cancel-settlement.md)。
