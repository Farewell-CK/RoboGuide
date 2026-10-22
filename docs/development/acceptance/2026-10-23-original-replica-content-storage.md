# 原内容存储与实际 reader 生命周期

- 日期：2026-10-23
- 分支：唯一 `codex/nonrelease-remediation-closure`
- 基线：`a0c2ce130b61f19c8c9d8012ffa59664bad64dfc` + ADR-0159 首步
- ADR：[原内容多数派与 Raft 引用生命周期](../../decisions/0159-original-content-quorum-and-raft-reference-lifecycle.md)
- 范围：member-local 内容存储；生产部署 v0.3 仍只复制元数据。

## 实现边界

`controller-replication/content.rs` 固定原 owner/digest/size 与实际接收 store，检查原
profile/epoch/revision/checkpoint/batch/StoredMembership。只有同一成员提供完整 metadata
与完整内容集合才计票；joint 的两组分别过半，learner/重复/冲突/旧 epoch 不凑数。
这些 DTO/单元检查不认证 TLS、不读取文件，也不产生成功 ACK。

`content_store/` 是另一个实际软件 backend。明确 fresh create，实际 root/SQL inode
双 writer lease，原 `.content-creation.json`、owner marker 和 regular-file 旧路径 fence；
原 owner 为接收软件身份，独立于内容原 `artifact-store:` owner。相同 bytes 在不同
原 owner 下有各自 qualified namespace。SQL 使用 DELETE/FULL，完整 mandatory heads/
rows、closed schema 与固定上限；丢表/行/head 不隐式变成空 authority。

copy 的原实际 source writer、receiver、命令、原 Linux boot/time/PID namespace、PID/
start ticks 和预算先于 bytes 持久化，receive pin 同事务建立。每 chunk 重查原预算/
source writer，完整实际 hash/size、文件/目录 fsync 后才提交原 receipt。原命令 retry
只读历史。新的显式 storage repair 有独立 intent/预算，不改写旧 Unknown。

managed reader 在文件读取前持久原 intent/pin，全生命周期持有实际存储 lease。
实际原 FD 关闭先于原 close SQL receipt 与 pin 退休；完整/部分 bytes 的实际 hash/
count 分开，过预算不报告 complete。SQL 失败或 Drop 不伪造 durable close。
FD 总在最后一个存储 lease 释放前关闭。重启后的 Unknown 自有 pin 保留；显式
process-end 只能在持有原 root/SQL lease 后独立核对原实际 boot/PID namespace/
PID/start ticks，不从时间或原 Cancel 推断。进程消失只退休其 own pin，不补造
原读取/复制 receipt、OS wait、任务或设备停止证据。

## 验证

`cargo test -p controller-replication --lib --locked`：**54 passed**。含 actual files/
SQL/leases/managed readers，另有明确标注 synthetic 的 quorum/reference 语义反例。
Clippy 全 workspace/targets、345 文件 strict mypy、536 文件格式、Ruff 和函数文档检查
通过。全 workspace Rust 为 **992 passed、2 ignored**，随后收紧 final hash/SQL 原预算
门禁并新增真实过期 reader 用例，受影响的整个复制 crate 54 项复测通过；未把此前的
全仓计数冒充最后新增用例后的同一次全仓结果。私有 Rustdoc 和站点 9 项测试通过。

实际独立进程 gate：

```bash
uv run python tools/quality/check_replica_content_processes.py \
  --probe target/debug/examples/content_store_probe \
  --profile 2d14edd6619be91bcec675bb0e53a5fcb4590a662f1a0548e8eb009edcb8f867 \
  --member 23 --payload-mib 64 \
  --output /data/workspace/roboguide-evidence/nonrelease-closure/p1/replica-content-clock-final
```

完成结果以该目录 `summary.json` 的 actual process/wait、binary/source hashes 为准。
先前 `replica-content-processes-03` 以实际 128 MiB bytes、另一个声明 member 17 通过
**33 个实际独立进程**；`replica-content-original-final` 增加完整 PID namespace fence，
`replica-content-bounded-final` 再加入 final hash 的原预算检查，均以新目录 33 个进程通过。
最终 `replica-content-clock-final` 还覆盖 final SQL writer/commit 前的原预算复查。
检查覆盖原 intent 后真实 SIGSTOP/SIGKILL/wait(-9)、源/目标 live writer 拒绝、原
Unknown retry、真实 SQL `BEGIN IMMEDIATE` 阻塞 final commit、独立 storage repair、
原 reader kill/restart、真实 complete/partial read、原 clone 与 hardlink SQL writer
拒绝、旧 Artifact constructors 拒绝、真实字节损坏与 mandatory reader 行丢失拒绝。

保留的失败：`replica-content-processes-01.log` 的显式 profile 长度输入错误发生在
create/spawn 前；`replica-content-processes-02` 保留实际只读 SQL lock 竞争与 killed
wait，检查器最初未重试此观察错误。随后只对明确 SQLITE_BUSY/LOCKED 在原 deadline
内有界重试，未放宽产品写入或 Unknown 规则。早期 unit 的只读 CAS corruption fixture
需先对其 own 临时文件显式 chmod；产品 finalized bytes 仍只读，失败记录保留。

## 仍须完成

此 gate 没有启动 Raft、网络 listener、Controller、Node、设备、模型或模拟器。实际
基础设施 member 的一个整数声明不生成任何机器人事实。尚须接 log/view/Snapshot
真实生产与释放、mTLS chunk/观察、同成员多数 ACK、完整 Catalog/Archive/leader
内容、cold restore、旧部署排空升级/回退和配置驱动 Node/Mission 换主。N2、N4–N7
全部条件保持；不宣告在线 HA、跨主机 RTO/RPO、物理连续性或 SoftwareComplete。
