# 已归档原源的首次副本初始化

本功能把一份已排空的原 Controller SQL/CAS 引入尚无应用状态的副本集群。它保留原
checkpoint、命令、热尾、Archive/Catalog、来源及 Unknown；不补造已压缩事件，不创建
Node 或设备。设计见 [ADR-0195](../decisions/0195-original-archived-source-initialization.md)。
完整进程、运行入口与恢复验收记录单独列出，不能用本页的配置说明替代通过证据。

## 部署声明

先排空原写入者，保留原 SQL、CAS 和部署输入。源 SQL 必须仍有完整命令和连续 Archive
前缀；源 CAS 必须是已存在且完整的 managed retained layout。初始化不会创建、升级或
修补源目录。原已复制集群的跨 profile 恢复不走 standalone 接管例外。

将原绝对路径写入一个 JSON 文件，字段为 `source_sql`、`source_cas`，然后执行：

```sh
controller-replica --inspect-initialization-source original-source-input.json
```

该只读命令实际持有源 SQL sibling/inode 与 CAS 租约，返回完整源 digest、原 CAS owner、
归档 floor、checkpoint sequence 和最后命令 revision。它不提交初始化，也不证明当前
quorum 或内容复制。部署负责人把返回的 digest/owner 固定到所有成员相同的
`content.initialization`：

- `schema`：`roboguide.content-initialization-policy/v0.1`。
- `source_state_digest`、`source_owner`：上述原观察值。
- `source_member`：实际部署中的原来源投票成员，不是机器人选择器。
- `command_id`：本次唯一原操作，最多 128 字节。
- `budget_ms`：首次整体预算，100–300000 ms，包含排队、读取、传输和结果提交。

只有该来源成员配置 `content.initialization_source`，其值为原路径声明；其他成员留空。
所有成员显式选择 process v0.7、parent9 与 Root8。现有 parent8 必须真实排空后执行
`--upgrade-initialization-parent <config.json>`，保持原 SQL inode。普通旧入口不升级它。
没有初始化声明的显式 parent9 部署保留普通应用策略；不能借此接纳初始化候选。

`node_tls`、`operator_tls`、`artifact_tls` 是可选的独立部署输入，每项使用
`roboguide.controller-transport-tls-inputs/v0.1`，包含 `server_cert`、`server_key`、
`client_ca`、`profile` 四个原文件绝对路径。共同策略固定文件字节，允许相同字节换路径。
Node profile 沿用原身份注册表，另外两项沿用各自原授权格式。生命周期 Node 身份仍需
Artifact 证书授权；Operator TLS 与 loopback 开发管理员不能混用。文件不注册或启动 Node。

## 实际执行与失败

来源成员必须成为实际 leader。supervisor 在应用尚空时可通过原 leader-transfer 路径
交接至该成员，仍使用实际 no-child/原 wait 证明。全部当前 voter 的实际 mTLS 连接先确认
parent9 和完全相同的策略；只有多数支持不够。

来源成员的单例操作日志先保存原 ID、输入、epoch、kernel/process 时钟，再读取源。
完整候选只捕获一次，随后沿用同一原预算，经过正常字节传输、Raft Commit 和完整内容
quorum。取消等待不释放仍在执行的任务或租约。相同 ID 重试只返回原记录；原进程死亡、
响应丢失或预算结束后，缺少原结果仍为 Unknown。当前已接纳状态的独立观察不补写原结果。

有初始化声明而应用尚空时，supervisor 不启动空 Controller。原 standalone 检查点保持
原字节；实际当前 child 通过自己的 PID/Unix/mTLS 和新鲜完整 quorum 核对精确初始
checkpoint/anchor，才执行一次普通持久化启动提交绑定新 profile。不同 profile、变化的
checkpoint、后续 checkpoint 和通用恢复均不能使用这个例外。子进程二进制必须先通过
`--replica-initialization-contract` 声明显式支持。

后续普通增量提交继续携带原初始化 anchor。Log purge 和 Snapshot 不删除它；它也不为
已退休源内容新增 pin。已接纳或恢复后的应用不重新读取源文件。

## 恢复与证据边界

显式 recovery v0.23 要求完整 parent9/Root8 配对、原部署文件及当前所需的同 Root CAS
字节。它保留初始化 policy、完整原操作、capture、结果/Unknown、预算和原 backing
历史；Raft Snapshot 不导入或抹掉这些 member-local 操作。旧 recovery profile 拒绝
parent9，restore 不启动服务或续期权限。
若原来源有已完成的 Archive 操作，清单还须明确声明原 Source CAS 和 Archive destination，
以及它们与所有 Controller 投影的原 writer 依赖。接纳元数据不能替代独立归档目录。
v0.23 只在独立核对到同一实际 Replica profile 且完整原记录一致时复用重复投影的校验。
缺失/冲突仍在备份或 verify 前拒绝；原 SQL 中的路径和历史不改写成恢复后的新事实。

原来源包含的软件 Node 历史与本次创建 Node 是两回事。当前门禁创建零设备、模型和
模拟器；软件进程退出、TLS、复制和恢复不证明设备停止、物理连续性或整体 SoftwareComplete。
