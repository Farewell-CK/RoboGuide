# Replica 同一成员证书轮换 v0.1

状态：当前有界软件路径已验收，见[原记录](acceptance/2026-10-20-replica-certificates.md)。
原理见 [ADR-0116](../decisions/0116-replicated-member-certificate-rotation.md)。
这项功能处理基础设施成员 leaf 与部署 CA 材料迁移；Node、Mission、资源和设备事实保留各自权威。

## 部署前提

显式配置 `replica.schema = roboguide.controller-replica/v0.3`，声明 3–9 个非零、独立端点
的基础设施成员与 `initial_voters`。必须使用 mTLS；`tls.cert_path` 和 `tls.key_path` 指向
同一个私有 PEM 文件，文件同时包含该成员的完整证书链与私钥。通过新 inode、fsync 和
原子 rename 更换 bundle；不能分两次覆盖 cert/key。材料路径、DNS、bootstrap pins 与原
cluster/application profile 保持一致。CA 文件按下述显式 overlap 流程更换，不用增添
root 代替对 exact leaf 的授权。

初始 pins 是 profile 绑定的 bootstrap，不在轮换时改配置。当前可用/退役 leaf 在原
`replica.sqlite3` 的 Raft authority，随同一事务/快照持久化。缺失、损坏、旧 revision、
历史分叉或不匹配 bootstrap 均拒绝；不得恢复成旧配置 pins。旧 v0.1/v0.2 不夹带该权威，
旧数据迁移必须走后续显式升级协议，不能把 schema 字符串改成 v0.3。

## 原操作

使用本地原成员配置及原数据库读取 quorum-current trust：

```bash
controller-replica --admin /absolute/member.json --leader MEMBER --certificate-trust
```

生成并由部署 CA 签发新叶子证书，记录 DER 的 `sha256:` digest。新文件可在部署后生成，
无需启动时预埋第二张证书。写入闭合
[request](../../contracts/replication/certificate-rotation-v0.1/request.schema.json)：
原 `command_id`、当前 `expected_revision`、原成员、`stage`、当前及新 leaf。随后显式提交：

```bash
controller-replica --admin /absolute/member.json --leader MEMBER --rotate-certificate /absolute/request.json
```

Stage commit 只增加 infrastructure revision；旧证书仍有效。一次最多一个待完成 Stage，
新 leaf 不得属于任何成员当前/已撤销历史。部署随后原子替换目标成员的 bundle；原
listener、Raft client、管理 client 与子 Controller fence 在真实连接前读取它，不重启
Controller，不更新其原 epoch 或执行预算。

退休请求固定原 Stage ID、旧/新 leaf、成员及当前原 revision。leader 必须通过实际 TLS
联系**全部配置成员**；每个成员确认同一 Stage，并自己独立连接目标，要求目标在任何
应用字节前出示原新 leaf。leader 再绑定各回复的实际服务端 leaf，完整证明随 Retire
提交。调用者不能提供证明。缺一个成员、新 key、quorum 或原 Stage 都拒绝退休并保留
原 Stage；不擅自排除离线成员。

所有网络 mutation 在同一个 member writer 排队后复核原实际 TLS witness；Append/Vote/
snapshot 与只读 TLS probes 不等待这个 gate，避免退休等待自己的复制/观察。旧证书退休
后不再授权新 frame；旧 key 即使仍被 CA 接纳也被精确成员权威拒绝。
链和有效期在实际 TLS 后、排队取得 writer 后再次验证；始终使用实际出示的完整 chain，
不能用之后替换的文件为旧连接背书。

Stage 的新私钥丢失时，显式提交 `abort`，固定原 Stage/旧、新 leaf/revision。它不要求
已经丢失的新 key，而是永久退役该待启用 leaf、保留原 current leaf。若部署已切到被
放弃的 bundle，该成员会 fail closed，部署须恢复仍被授权的原 bundle；系统不回写文件。
同一 ID 的 Abort 只返回原 receipt，晚到 Retire 不能重新生效。

## CA 根迁移

为每个成员原子部署 old+new roots bundle。用新 CA 签发的 leaf 逐一走上述原 Stage、
实际新-key 验证和 Retire；有成员未装新 root 时，独立 TLS 证明失败，不能退休旧 leaf。
全部成员转到新 CA 后，才逐一原子移除旧 root。所有原始 leaf 的退役仍在 Raft history，
单纯重新加入旧 root 不能恢复旧成员凭证。TLS 文件最多 1 MiB，原 roots DER 排序/去重
后按长度前缀求 digest，格式差异不创造不同根集。

通过实际目标连接读取该 endpoint 的本地材料和原 trust revision；该只读结果不声称
quorum，须与已知 quorum-current revision/digest 核对：

```bash
controller-replica --admin /absolute/member.json --leader TARGET --certificate-observation
```

返回实际 served leaf、实际 roots digest、local revision 和 trust digest。Raft 客户端与
子 Controller 每次真实连接刷新相同路径，材料错误不退回旧缓存。该协议不经营 CA 私钥
或自动签证，也不把软件证书当作硬件身份。

## 恢复与容量

超时不等于失败或重新轮换。查询原 trust/receipt；相同主体与完全相同 command 返回原
不可变结果。主体自己的证书后来轮换，可用当前凭证读取原结果，原 caller leaf 仍保留。
相同 ID 改字段/换主体拒绝。所有历史保留，已退休 leaf 永不重新 Stage。

本有界版本最多 512 个接受命令，即最多 256 个完整轮换，不自动删历史扩大额度；遇界限
须明确升级，不能复用 ID/证书。所有配置成员参与 Retire 是本 profile 的可用性取舍。
离线成员恢复原日志/快照后才参与；新路径、冷副本或历史缺失不提供隐式重新授权。

原 quorum/leader-transfer/旧 epoch fence 不改变。合法普通 timer 仍可提交自己的零事件
application revision；证书命令本身不改应用 checkpoint/revision。SQL/快照回归单独验证
这一点，实际进程 gate 另核对完整 checkpoint 和原事件。

## 验收

```bash
uv run python tools/quality/check_replica_certificate_processes.py \
  --output /absolute/new-proof-directory --old-binary /absolute/old-controller-replica
```

该 gate 启动实际三副本和实际 Controller child，创建零 Node、零设备、零模型、零模拟器。
真实检查 Stage 后未换 key、缺一个成员、实际旧 key mTLS 拒绝、leader 原进程换证、旧
二进制拒绝和全体重启。还验证新 CA 缺席、所有独立 root 文件迁移、旧 CA 的实际 TLS
拒绝及真实丢失私钥后的 Abort。它不把轮换当作二进制滚动升级、全量 HA/CAS
恢复或物理连续性；这些仍是 F5 和后续阶段的必需交付。
