# Controller 写入禁用与恢复操作

本指南对应 [ADR-0114](../decisions/0114-write-disabled-controller-recovery.md)，用于本地 SQL
故障后保留查询和明确恢复。它不是跨站点接管、整体生产验收或设备停止证明。

## 故障期间

运行 Controller 在原 writer 下锁存第一次持久化错误，进入 `write-disabled`。后续 mutation
返回 503，timer 不推进派生工作；Node 输入得到 application unavailable，不能形成 durable
Registered/ACK。连接及读取服务保留，查询原提交状态；数据库本身不可读时，相关 SQL 查询
仍可能返回 unavailable。State 原 received_at、TTL、所有权、attempt 和预算不改写。

Operator 证书、稳定主体、角色和 tenant 检查继续执行。grants 文件若与已提交 authority
不同，禁止借恢复入口隐式接受新授权；先解决部署/权威不一致。复制 profile 的私有读取
仍核对当前 epoch/quorum，原 watchdog 的失去多数派退出规则不变。Artifact 私有读取和
写入仍要求原持久审计；未知存储期间拒绝，不虚构 audit success。正在传输的 CAS chunk
在原 writer 下重新检查禁用状态，之前通过 HTTP admission 不授予继续传输权限。

`/healthz` 表示进程仍在运行，`/readyz` 返回 503。Prometheus
`roboguide_authority_write_enabled` 为 0，`roboguide_authority_disabled_generation` 保留原
代际；`roboguide_storage_read_available` 区分 SQL counts 是否可读，不可读 counts 为 NaN。
这些指标不把最后上报的设备 Online 升级为当前可运行。

## 本地 SQL 已恢复

1. 保留数据库、原目录、日志和故障触发证据，修复实际空间/权限/设备或 SQL 故障原因。
2. 以实际授权证书读取 `GET /v1/authority-status`。原 `failure.restart_required=true` 时
   跳过进程内探针，进入受控重启/重放；不能更换 command/process 字段强行继续。
3. 将原 process_id、generation、checkpoint_identity、authority_revision 原样带入
   [恢复命令](../../contracts/recovery/authority-v0.1/command.schema.json)，指定新的幂等
   command_id，通过同一 HTTPS Operator 入口提交 `POST /v1/authority/recover`。
4. 保留原 receipt，重读 status 与 readiness。命令重试只观察同一原结果；旧成功 receipt
   不表示新一代故障已经解除。禁止改 key 重做 Mission、Execute、import 或模型调用。
5. 恢复入口只提交原 checkpoint/command probe；后续正常 timer/fact 才按原权限和期限
   继续。已过期许可、Unknown、stop/handoff hold 和恢复门禁不会被这个探针清除。

400 表示封闭输入不合法；403 表示实际主体/当前角色/范围不足；409 表示原 process、
generation、checkpoint、journal 或同键输入冲突，或必须 startup replay；503 表示实际
存储或 projection 仍不可用。不同状态都不授权物理停止、资源释放或补发副作用。

## 必须重启重放

复制批次可能已被 quorum 接纳而本地 Commit 未知时，保留旧目录和全部原预算，使用已有
replica supervisor 从真实 accepted state 重物化，禁止本地缓存覆盖新权威。catalog 安装
fence 也通过完整事件重放恢复。若声明存储损坏，按[静止恢复工具](../../apps/recovery-tool/README.md)
验证完整 SQL/内容和 pinned manifest，恢复到新私有目录。迁移绝对路径、上线切换和跨域
恢复另受其部署合同约束，备份 receipt 本身不能授权启动。

## 实际软件门禁

```bash
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/roboguide-authority-recovery-new --authority-recovery --content-reclamation
```

此命令启动实际 Controller、两软件 Node、文件后端与独立 verifier。保存及 Commit 两个
真实 SQL 故障窗口核对原完整 SQL、query/source time 区分、Node unavailable、权限/异参
拒绝、原探针与幂等回执，随后完成原正式文件任务与回收/重启。不接设备、模型或模拟器。
故障检测/人工响应 SLA、告警路由和整个部署的 RTO/RPO 仍由运维 profile 和实际演练验收，
不能由本机及时解除一次 SQL trigger 推断。
