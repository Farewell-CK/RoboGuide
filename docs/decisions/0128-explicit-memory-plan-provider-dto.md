# ADR-0128：显式 MissionPlan v0.9 的 provider DTO

状态：Accepted，当前软件路径与实际进程组合验收通过。

## 已确认问题

实际原 Request、Memory 许可和自动修订组合验收在 provider 调用前失败：配置明确选择
MissionPlan v0.9，但 `provider_mission_plan.py` 仍将 canonical schema 限死为 v0.8。
原结果及进程退出记录保留在 `/tmp/rg-scoped-memory-runtime-mi-20261021-a`。
Controller 和 Python MissionPlan parser 已支持该语义合同，不能以单独路径通过视为整链完成。

## 决定

provider DTO 显式支持已维护 canonical v0.8 与 v0.9。默认仍为 v0.8；v0.9 必须配置选择，
沿用严格 scalar parameter entries/null sentinel 的 provider projection，保留全部原语义
Memory requirements 与 selector。返回 v0.9 必须对应原 v0.9 schema；不允许借兼容转换
删掉 prerequisites。原 v0.8 继续保留 v0.7 actor 输出兼容；不同 schema 的输出相互拒绝。
最终仍经过 canonical schema、MissionPlan、Catalog、Review 和当前 Controller 权限校验。

模型提供候选和意见，不选择 Node、provider、路径或资源，也不产生可用、停止、Task 满足
和 Mission 原目标证据。原 Request 必须先于执行，非空 child 声明先于 MI claim/provider
nonce，完整原停止/撤权先于正常 Control 新组/每 Task import/use。实际软件进程验收使用
明确标识的本地 Responses 协议 fixture，不声称模型质量；物理设备和实验均不运行。

Controller wrapper v45、Node 合同和数据 schema 不变。完整 F8、其他非发布整改继续按
各自必需验收闭合，不能从 DTO 支持推断全部整改完成。
命令、数据与失败保留见
[运行期 MI 与 scoped Memory 整链](../development/acceptance/2026-10-21-runtime-mi-scoped-memory.md)。
