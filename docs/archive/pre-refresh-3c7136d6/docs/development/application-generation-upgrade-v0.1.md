# 应用代次升级与失败修复 v0.1

本模块落实 [ADR-0207](../decisions/0207-explicit-application-generation-upgrade.md)。
Replica 的原 profile、原配置策略、原应用 schema 和 Root owner 保持不变；
允许当前应用写入的二进制和 schema 由独立、持久化的升级历史决定。
基础设施替换、应用升级、进程退出和设备停止分别保留自己的证据。

## 配置与存储

显式 process v0.8 在原 process v0.6/parent8 或 v0.7/parent9 上增加
`application_upgrade`。`base_process_schema` 保留原布局；`binary` 和 `target`
声明下一程序的绝对路径、完整 SHA-256、应用 schema 和完整策略摘要。
可选 `retained_binaries` 保留已经启用的程序，连同候选程序最多 32 个。
这些路径只是部署输入，网络请求不能提交新的执行路径。

supervisor 独立读取实际程序的 `--application-schema` 和
`--application-upgrade-contract`，核对完整文件哈希，再保留实际 inode、长度、完整
mtime/ctime 和 mode witness。替换或改写文件必须重新启动并重新验证，不能沿用缓存。
原策略摘要仍由原程序、配置、TLS、Catalog 等原输入计算；候选程序的策略单独计算。

有限读取窗口为 Controller v53–v56。读取旧检查点不会授予旧程序写入权。
第一次实际升级 intent 在原 SQL 事务中安装 parent10 fence，保存原 parent8/9 解释、
成员本地 intent/原预算/原进程日志前缀，以及独立的已提交 authority head。
Root8、Controller wrapper v56、Node 合同和 Node journal 的语义不变。

## 正常升级

逐个停止并 wait 原 follower supervisor，再启动新基础设施；原 leader 继续服务。
随后使用现有 leader transfer 的原 child 退出门禁完成 handoff，再替换原 leader。
这一步允许新的基础设施继续托管原应用，不自动启用候选应用。

管理员通过实际 mTLS 入口提交一次闭合请求：

```text
controller-replica --admin <process.json> --leader <member> --application-upgrade <request.json>
```

请求固定 `schema=roboguide.application-upgrade/v0.1`、原 `command_id`、
`expected_revision`、`from_schema`、完整 `target` 和 100–300000 ms 的原预算。
所有配置成员必须独立返回实际 TLS leaf、基础设施程序哈希、完整 membership、
相同候选程序和相同应用 predecessor；支持采集完成前不发送 drain。

每个成员先持久化原 intent，再阻断应用 spawn/ingress/Commit/投递。supervisor 只停止
自己持有的 child，并保存原 PID/boot/start-tick 的实际 OS wait；没有 child 时，
也必须保存原 supervisor instance 和已确认完整进程日志前缀的 absence proof。
全部原 wait、原 epoch/quorum、原 membership、原 predecessor 和原预算仍成立时，
才允许一条 Raft activation。丢失应答不能说明 activation 没有效果。

新 child 只可由与最新 activation 完全相同的实际程序启动。它读取旧检查点时，
还必须核对最新 activation 固定的完整 checkpoint hash、event sequence、revision，
以及自身实际程序哈希。正常恢复路径随后提交新检查点，原检查点从未被直接改标签。
归档源首次初始化另走原 source anchor 的精确检查，不能借升级兼容跳过它。

## Unknown、显式放弃与补丁

```text
controller-replica --admin <process.json> --leader <member> --observe-application-upgrade
controller-replica --admin <process.json> --leader <member> --abort-application-upgrade <original-id> <abort-id>
```

观察不重发 stop 或 activation，也不更新原时钟。重启后原日志可能已经提交，应先核对
原结果。只有仍被实际成员保留的原操作才能显式放弃；放弃由当前独立 quorum 排序，
不允许撤销已启用版本，也不能解除原未确认进程退出的 hold。原 intent、预算、wait
继续保留。完全相同的已提交结果可读取复用；不得给 Unknown 另发同 ID 的新预算。

应用启动拒绝或意外退出时，基础设施和管理入口继续运行。supervisor 保存实际退出，
输出最多 1024 字符的 `application_error`，在本 supervisor 的相同 epoch/已启用代次
下只尝试一个应用 incarnation。新 activation、新 epoch 或显式重启才允许下一次尝试。
原 spawning/unreaped fence 始终独立成立。

同 schema 的修复必须使用新命令、不同实际程序和新的完整策略；保留已启用程序供
原历史核对。原完整策略 witness 不可替换，缺失时拒绝同 schema 补丁；所有已启用过的
程序哈希和原部署策略均禁止作为新目标。失败版本尚未迁移的旧检查点，可以继续由
新补丁精确固定为 predecessor，不得改写失败版本的原 activation。

## 恢复与验收

完整静止恢复使用显式 recovery v0.25。所有实际 writer lease 先于源读取，验证完整
parent/Root/Controller/Artifact/CAS 和实际部署输入；原已启用、已放弃历史及成员本地
原时钟、原进程前缀和 wait 必须一致。Snapshot 只传播已提交 authority，不能导入或
删除本地 stop/wait 权威。缺少 mandatory history、借用另一成员的 wait 或退回旧恢复
profile 均在发布前拒绝。restore 不启动服务。

维护入口为 `check_application_upgrade_processes.py`、
`check_application_patch_processes.py --abort-before-patch`、
`check_application_upgrade_reconciliation_processes.py` 和
`check_replica_content_recovery_processes.py`，均位于 `tools/quality/`。
实际证据见[验收记录](acceptance/2026-10-25-application-generation-upgrade.md)。
三成员软件进程、原文件和独立 SQL 读取只证明该软件协议；这些入口不创建 embodied
Node、设备、模型或模拟器，也不证明物理停止、物理连续性、跨主机部署或长期 SLO。
