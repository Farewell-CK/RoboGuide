# 静态 Node 身份在线采用：2026-11-09 软件验收

基线 `1d9a9ad5`，修复分支 `codex/rg-hot-enrollment-maintenance`，合同见
[在线采用](hot-node-enrollment-v0.1.md)及 ADR-0239。本次未连接机器狗、未修改 ThinkPad
现场服务，未验证硬件动作、PhysicalEntity 在线登记或 DeploymentFeasibility 在线切换。

## 已验证

- 7 项新增测试覆盖原始绑定和完整归属、预 Bind 资源占用、重复回执、SQL 回滚、
  权限拒绝、生命周期投影和原 checkpoint 恢复。
- Domain / Control / Integration / Orchestration 共 382 项库回归通过，2 项原有测试忽略。
  integration-server 全部 318 项测试通过，包含原有身份回归及新增 HTTP 测试。
- 工作区 all-targets/all-features Clippy `-D warnings` 通过；脚本 Linux strict mypy、
  Ruff、函数文档检查通过，Rust 格式及 diff 检查通过。
- `check_hot_node_enrollment_processes.py` 使用真实 Controller、两个通用 Node、独立
  软件 CA/数据库及只读假源，完整通过。原 Node 在任务 Running 且持有资源期间采用；
  原热阶段 PID/session 未变，新 Node 在注册授权后重连入群并完成独立 Mission。
  之后旧任务完成，两条原 Attempt 都是 Completed，占用及 Unknown 为零。
- 冷恢复沿用原静态字典，动态新增 Node 仍能重连；重复采用返回原回执，未改写历史。
  使用真实旧版 Controller 读取数据库副本，明确拒绝 unknown field `adoption`。

## 原始证据

公共结果及过程日志保存在 Windows `lab/rg-maintenance-audit-20261109/`，完整私有软件
凭据/数据库仅保留在 WSL `/home/aios/.cache/rg-hot-enrollment-gate-20261109-accepted/`。
密钥未加入仓库。公共 `hot-identity-process-accepted.log` 及 `hot-identity-result.json`
记录 PID、原 session、两条 Attempt、程序及脚本 SHA-256。

| 项目 | SHA-256 |
| --- | --- |
| 当前 integration-server | af2051f292999ba9c52a1f970830ced555041e7769293a67691775cd4a511c7c |
| 当前 roboguide-node | 35a20fc7dd0d0bf66e61cb606d943e62471ad1ec3fc77f6f4dda7070f5061f3d |
| 旧 Controller（现场 2d6a043c 归档程序） | 7a01be0085643706da2eed7b48b2955d092c42a43fd75a735205eaf1d684a403 |
| 验收脚本 | dbc18443d91bb8e34ba2ff0689cfa4037ccad334c12281188d8801fa89c3b88d |

首次进程验收失败记录仍保留：旧字符串角色配置默认只授权 default 租户，导致 inventory
过滤；随后测试在新 Node 首个 Online 心跳前提交，任务等待；数据库备份连接未关闭则
触发原有 quiescence 防护。修正测试的显式授权、Online 观察和连接关闭后整组通过。
这些测试修正未放宽生产权限、健康或数据库写入边界。

## 后续边界

RG-HOT-001 完成软件验收。RG-HOT-002/003 仍开放；尚不能据本次结果宣称新物理实体
及 MI 静态可行性快照也已支持全链路在线更新。现场升级需要另行核对部署版本和恢复输入。
