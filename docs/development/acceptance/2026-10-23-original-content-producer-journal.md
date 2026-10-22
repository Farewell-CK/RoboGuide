# 原内容生产者：原日志、实际作用域与中断对账

- 日期：2026-10-23
- 唯一开发分支：`codex/nonrelease-remediation-closure`
- 基线：`4a252db2c8e3551cbe0cde051b358dc3a5c1db2b`
- 决策：[ADR-0160](../../decisions/0160-original-content-producer-journal-and-interruption-reconciliation.md)

## 实现

显式 content SQL v3/layout v0.3、Raft storage v5/schema v0.5 接通实际 Log、View、
Snapshot build/install 的原 operation journal。原 v1/v2 copy/reader 的入口、backend
identity v0.1、application v3、Controller wrapper v52 与 Node 合同保持原义。
旧 parent v4 不自动升级，新格式不能借旧 reader open 获得权限。

每个原 intent 与全部独立 pins 同一 FULL 事务，保留真实 source SQL descriptor
device/inode、完整输入/epoch/parent digest、原 PID/boot/namespace/start ticks 和
不可续预算。不可 Clone 的 managed permit 持有确切原 writer 类型；原 publication
只来自实际 parent Commit 后的完整独立核对。实际 scope close、进程 absence、
原 publication 和独立 observation 分开保存，不把任何一项补写成另一项。

当前 View/Snapshot 的 mandatory 本地 heads 与 parent metadata 同事务。missing
head/原 journal row 拒绝打开与对账，不能变成“没有引用”。head、copy、reader、
transfer/child journals 不由 Snapshot 导入或清空。接收方有自己的原 intent；其
实际内容在安装前独立验证。已发布 Snapshot 的替换不结束旧 reader。

同 ID 只观察原结果，Unknown/已退休 ID 无法重新写入。新的显式 storage repair
有自己独立的原 ID/预算，早先 Unknown 仍保留。原 producer 被实际 parent 删除/
替换且原 worker scope 已结束，或有独立原 process absence，才退休自己的 pins；
Receive、Reader、其它 View/Snapshot/Log 仍分别保留。

新 producer profile 的异步重型 append/apply/Snapshot 入口将实际 source/root
leases、原排队前时钟和容量移动到实际 blocking worker。每个实际 store 最多八个
运行/排队 job，超限在 intent/effect 前拒绝。取消等待者不关闭仍运行的原 worker。
同步工具入口使用同样的容量与原时钟，不声称网络调度证据。

## 实际证据

`original-producer-native-02/result.json` 的 **39 个独立 owned process** 已通过。
源数据来自 `original-producer-native-01` 中真实 Controller/mTLS 上传及原 SQL/CAS；
probe 不插入替代 upload row，不创建 Node/设备/能力/模型/模拟器。

- 原 Log/Snapshot 在 intent 后实际 SIGSTOP/SIGKILL，native wait **-9**；重启保留
  原 Unknown/pins，原 ID 不重发。独立 source absence 与原 PID/start ticks 观察才
  退休相应 pin，原 publication 继续为空。
- 实际 SQLite writer contention 使 parent Log 已提交而 root receipt/close 写入
  失败，保留其原 native 非零退出。只读对账记录 observed referenced，原结果仍
  Unknown；实际 truncate 后才独立退休，未补造 within-budget completion。
- 独立 target 缺 bytes 拒绝 install；实际 qualified CAS copy 后，旧 ID 仍拒绝。
  新显式 repair 才正常安装，保留原 missing-content history；发送者的原操作 IDs
  没有进入 target 操作权威。
- 两个实际 old/current Snapshot readers 在替换后独立保留；实际 kill/wait 后
  分别观察原 process absence，receipt 仍为空。Log、View、Snapshot、Receive
  独立 pins 仍保留；缺 mandatory parent head 或 producer row 拒绝。
- 真实保留的 `legacy-content-snapshot-probe-4a252db` 创建原 v2 View/Snapshot/
  readers，live writer 时 upgrade 拒绝，实际 native wait 0 后升级。全部 raw
  reference/reader/copy/object rows、原 owner/预算不改；旧 binary 拒绝新 SQL。
  原 parent v4 继续拒绝隐式升级，legacy 缺失的原 source/预算明确 unavailable。

旧二进制 SHA-256：
`e8926671f23ddb7c18b648302f017ead1f62f60b6bb38e9ac3605c7fecb61e63`。
CI 没有历史 executable 时使用显式原 v2 profile，结果明确 `historical_binary=false`，
不称为旧二进制验收。

定向 **10 项**实际存储检查通过，覆盖 append/apply SQL rollback、exact readonly
retry、原 applied pointer 不回退、独立 reader、缺 bytes/head、真实时钟到期、队列
上限及单线程 Tokio 的真实 parent-writer 阻塞/取消。它们不等于网络 quorum。
最终全仓回归、完整 source freeze 与外层 Archive/cold 组合按下述独立结果补录。

最终 `original-producer-frozen-final` 已通过：完整 source freeze 前后相同，固定工具
二进制 `content-snapshot-probe-adr0160-final` SHA-256 为
`d30f154fa1e00e89b6c450fd5a3445adc4ba002641cb6763872c5c32f2b96ac9`。
其子流程仍是 **39 个**实际进程；另有原正常 Snapshot 的 **10 个**进程、完整 State
capture/materialization、原 Archive abort/SQL rollback/retry 和 v0.16 cold recovery
全部通过。该 cold profile 不包含新 producer roots，不代替后续完整 HA 恢复。

全仓 Rust **1013 passed、2 ignored**；完整 targets/features Clippy、private Rustdoc、
349 文件 strict mypy、540 文件格式、Ruff、函数文档、MkDocs strict 和站点 **9 项**
测试通过。Site 单独检查需 `PYTHONPATH=website`；完整 mypy 使用 CI 的显式 recovery/
deployment/spatial 等维护范围。未将早先缺导入路径的检查计为产品失败或通过。

首轮 `original-producer-native-01` 因缺内容诊断只有 SQL `Query returned no rows`
而拒绝验收；产品已提供明确的原 qualified content unavailable 诊断。
`original-producer-native-final` 因并行 cargo test 重建 probe，原哈希门禁拒绝；
该失败保留，不算通过。固定二进制后使用新目录重跑，不覆盖原结果。

## 仍须实施

本包验证显式 fresh producer/storage profile，未启用生产 ReplicaConfig v0.4。
旧 v2 active legacy pins 原样保留，缺原 source/budget 不补造；原 parent/全部
部署的排空、完整 adoption/迁移和回退仍属 N1/N3。没有把拒绝升级称为升级完成。

真实 mTLS byte transport/实际 member store 注册、同一 current/joint voters 的完整
bytes+metadata 多数及 ACK 前重查、Artifact v6 owner alias/Prepared source、online
Catalog/Archive/Snapshot/GC/recovery v0.17、Node/Mission leader routing 与实际文件
原目标 failover 继续必需。Snapshot reader Drop、其它同步路径、真实 single-worker
heartbeat/epoch 与 Replica listener 的独立容量/帧内存/超时仍属 N7。

本地过程与存储结果不证明设备停止、跨主机 RTO/RPO 或物理连续性。N1/N3 以及
N2/N4–N7 原全部完成条件保留，尚未达到 SoftwareComplete。
