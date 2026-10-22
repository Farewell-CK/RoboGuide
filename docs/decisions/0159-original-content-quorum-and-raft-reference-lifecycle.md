# ADR-0159：原内容多数派与 Raft 引用生命周期

- 状态：Accepted，实施中；当前 v0.3 仍是元数据复制合同
- 日期：2026-10-23

## 决策与权威

完成原计划 N1/N3 尚缺的 Raft Snapshot/在线 Replica producer。采用显式新 Replica
profile v0.4、Replica storage v4、application-commit v3 与支持原 owner 副本映射的
Artifact storage v6；旧合同不隐式获得这些能力。Controller/Node 的任务、资源、
safe-state 和当前 use 权威不改变，基础设施内容证据不授予 semantic read/use。

原内容键包含原 store owner、完整 digest、size；相同 digest 不合并不同 owner 的
保留责任。接收成员另有实际 member/store owner/inode，不能把原 owner 填成接收
owner。supervisor 独立内容存储保存这些 qualified origins、实际 bytes 和操作日志。
leader 的应用 CAS 通过精确已接受的原 owner→实际本地副本映射读取恢复内容；复制
不能改写原 Catalog/lineage/上传/验证 evidence 或借某个接收方的身份补造源事实。

新 private application-commit v3 包含原 v2 semantic batch 与完整原 State recovery
projection，保留原 Archive authority、零操作 marker、原 copy/abort 的完整 raw rows、
manifest、Catalog archive head/rows 和原 history/timestamp floor。v0.4 保留完整原
未归档事件尾，不沿用独立删除 metadata prefix 的自动 compaction；达到 bounds 时须
先完成真实 coordinated Archive，不能把只剩 checkpoint/digest 的旧前缀猜回内容。
materialization 在同一 inactive SQL transaction 安装原 projection、完整事件/命令与
checkpoint，原通用 v2 materialization 不可补造缺失的 Archive ID/journal。迁移原
v0.3 已压缩部署须取得原完整数据；不能用 fresh profile 绕过缺失。此项保持 domain
checkpoint/Node 语义，新的持久化/私有传输权限由显式 profile/storage/codec fence 承担。

内容输入还包括 Prepared 上传的实际 frozen staging bytes，不能等到 finalize 才把它
当成需要多数持久的数据。无原 owner 的旧裸 digest 须有原事件/实际 backend 的精确
来源绑定；新 leader 的当前 owner 不能补成历史来源。actual member content store 与
应用 CAS 各保留自己的真实 UUID，bootstrap/变更须经实际 TLS 观察并在 Raft 中固定
实际 store 注册；配置的 member 数字不产生 Node/设备。复制读取只由确切原 candidate/
log/Snapshot/current-view 输入授权，不授予用户/Task 的 semantic read/use。

原 metadata revision/checkpoint/batch digest、原 issuing epoch 与确切当前 voting
membership 一起固定。应用成功 ACK 必须由同一批成员同时提供实际已应用 metadata
和实际持久 bytes 的独立观察，完整所需集合不能丢项。该同一批成员须超过每组 voters
的一半；joint configuration 分别满足两组多数，learner 不凑数。两份各自的多数
若不是同一批完整成员，不能组合成内容成功。ACK 前重查原 epoch/quorum/membership。

实际 mTLS 连接确定 member/leaf；profile/source/grant/current trust 由原 writer
核对，JSON 的 member/ok/hash 声明不产生证据。每个实际接收端在原 command/owner/
kernel 非续期预算和 intent 持久化后才接收有界 chunks，完整 hash/size、文件 fsync
及原 receipt 后才报告。原 Unknown 不重造 provider/read/Execute；同原操作只观察
或核对已写入结果。新的显式 storage repair 可处理余量，保留早先 Unknown，不能
续原预算或把 bytes 就绪解释为 Task 成功。

## Snapshot、归档与恢复

完整 accepted replica view 需要原 Catalog recovery envelopes、必要 archive authority
与原内容集合，不能只复制 application checkpoint 后猜 Catalog。原 member-local
transfer/spawn/PID/wait 和内容接收/读取操作 authority 保持独立，不能由 Raft Snapshot
导入、清空或刷新。原 Archive/copy 的 kernel、source member/epoch 与不确定 history
保留，不把恢复成员的进程当成原 copier。

Raft log、实际 Snapshot build/publication、每个外发 reader/接收操作与当前 view 分别
持有原 qualified pins。intent/pins 先于实际读、复制或开放 stream；已发布 Snapshot
仍保留所需 bytes。替换 Snapshot 不释放仍存活/Unknown reader 的 pin。实际完整
receive/install 与独立 original reader 结束证明才释放相应责任，超时/断连不代替证明。
managed reader 持有原存储 lease 生命周期；旧进程/FD 尚在时不能接受新 writer。

OpenRaft 的实际 SnapshotData 使用管理句柄保留独立 reader scope，reader/copy 的
member-local authority 不放进 Snapshot bytes。scope 关闭只证明该实际基础设施句柄
结束，字段明确 `retention_only`，不伪造 payload read/count/hash 或设备退出。新 scope
使用 content SQL v2/layout v0.2；原 backend identity v0.1 不改。显式全原 writer 排空
upgrade 验证原 v1 的完整 heads/rows，原 raw intent/receipt、IDs、预算与 owner 不
重写，仅原子增加 layout/SQL fence；旧二进制拒绝新 SQL，普通 open 不自动升级。

Snapshot install/learner catch-up 先校验并实际取得全部原内容，再原子接受 metadata
和完成 receipt；缺 bytes 维持不可用，不能暴露已就绪的 leader 应用。新 leader
materialization 保留原逻辑 Store/Catalog authority 和内容 owner 映射，不能每次换主
生成另一份丢失旧 provenance 的数据库。Archive/compaction 使用显式 coordinated
maintenance；现有 replicated 模式的拒绝保持，不能先删掉 guard 取得通过。

完整静止恢复显式声明 supervisor/Raft/application/content/CAS/Archive 的全部实际
writer、mandatory journals/heads 和 qualified bytes，沿用完整目录/原预算校验。
在线恢复、cold restore、原数据升级与控制的回退分别验收，不把前者的元数据复制
视作后者的完整证明。旧 profile/binary 必须拒绝新 storage/commit，而非丢字段。

## 实施和验收顺序

1. 原 qualified content、实际 member 存储与 log/Snapshot/reader 引用日志、严格完整
   quorum 验证；实际文件/SQL/进程故障验证原 identity 和 Unknown，不计为在线完成。
2. 真实 mTLS chunk/观察、原应用 batch/outbox ACK 及同成员多数接线；用真正缺字节、
   SQL/回包中断、少数派、joint 一组不足、旧 leaf/epoch 和真实重启作反例。
3. 完整 Catalog/Archive/Snapshot catch-up、失去原 leader 后读取原 bytes/receipts、
   learner 提升前内容门禁，以及保留 namespace/原 references 的完整 cold 恢复。
   Node/Mission 入口须配置驱动地找到当前 leader，保留原 attempt/journal/Request/
   幂等键与 Memory 预算；还须实际文件软件 Node/独立目标验证的换主组合，不能只
   通过零 Node 基础设施读取就宣告执行连续性。
4. 原 v0.3 部署的显式排空/迁移、policy/binary 升级和受控失败/回退；原 child 日志
   缺失、旧 writer、响应丢失及独立故障域矩阵仍为必需，不能从 fresh profile 推导完成。

全部在唯一 `codex/nonrelease-remediation-closure` 分支实施。实际基础设施可使用配置
声明的多个软件成员和空 Node 集合；不内建 Node/设备/能力/地图或模型。单机 TLS/SQL/
CAS/CPU 过程只证明其范围的软件事实，不宣称跨主机 RTO/RPO、设备连续性、物理停止
或 SoftwareComplete。N2、N4–N7 及原全部非发布完成条件不变。
