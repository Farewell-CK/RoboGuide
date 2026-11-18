# 2026-11-05 本机人工取消结案软件验收

实现范围见 [ADR-0233](../../decisions/0233-local-administrator-cancel-settlement.md)。
本次补齐 Local EAIOS 适配器的离线运维入口，不修改 Controller/Node 二进制，也不修改
Robonix Pilot 或底盘动作。原任务历史、原预算及 Controller 的独立管理员结案保持。

## 已执行检查

- Windows Python 3.13 与 Thor ARM Python 3.12 分别通过 36 项协议、任务与人工结案检查。
- Ruff 格式、lint、函数文档和生产模块 strict mypy 通过。
- 实际冻结 Controller release `8fd00c4d`、Node 和原 `7894bd6f` Controller 在 WSL
  隔离 loopback 环境通过自动取消、原管理员结案，以及新增的本机人工结案三种用例。
- 新用例先产生真实夹具计划与 UNKNOWN，停止适配器后运行实际离线 CLI，再启动同一
  journal 的适配器。Controller 收到原 Cancelled，认证管理员独立结案，资源归零。
  三项用例均只有一次原任务与一次 scoped abort；Controller 冷恢复保持结果。
- 冻结的前版 `fece46ea` journal 模块对实际 v2 数据拒绝启动，SQL 逐项保持；新模块
  对同一数据正常冷恢复。原 Controller 也对自己的新 settlement 字段拒绝且不改 SQL。
- 篡改原生字节、创建时间、回执、主体、删除回执、降级 journal profile，以及陈旧
  序列、跨任务、缺确认、不同结论和不同幂等命令均拒绝。人工结果不导出 no-effects。

早期新增 Windows 用例的 SQLite 检查连接未关闭，导致临时目录清理失败。检查连接
和 CLI 的只读连接现均显式关闭，完整检查重跑通过。WSL 与 Windows 环境后续分离。

这些是软件和协议事实，不证明真实设备停止。现场原动作的管理员核验与下一次动作
必须分别保存，不把新的人工 Cancelled 解释为原生 Pilot 曾给出终态。

原始非敏感检查结果保存于工作区 `lab/rg-stand-retry-20261105/`、
WSL `/home/aios/.cache/rg-local-settlement-20261105/` 和 Thor
`~/rg-lab/stand-retry-20261105/`。前版源文件与 SHA、软件进程日志及原命令回执分别保存。
