# 跨 owner Completed 上传退休验收

2026-10-24，对应 [ADR-0182](../../decisions/0182-cross-owner-completed-upload-retirement.md)，
前置 `6bd8e05`，继续唯一开发分支 `codex/nonrelease-remediation-closure`。

原 Completed 上传 A 的退休现在从 fresh complete-quorum View 定位完整 record
和精确原 namespace，经实际 Root integrity reader 验证完整 bytes/close，再在原
writer admission 中复核当前原来源与权限。receipt 保留 A，当前实际 writer 仍为
B。相同 hash 的另一 owner、Prepared、已退休但仍被 Catalog 保留的 key 都不能
代替它。standalone 保持原实际 CAS 验证；重复命令观察原 receipt。

本次不改 retirement v0.1/codec v31、wrapper v53、Node 或持久合同。内部完整性
读取不授予应用 read/import/use，不复制到 B CAS，也不改变原 Completed 或其它
消费者责任。Prepared 放弃和 Completed 退休两种原上传释放现均有实际链路证明。

## 检查

证据根目录：`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。
相关 **114 Replica + 277 integration-server** 测试通过；新增 counterexamples
包括 immutable record 改动、借用同 hash owner、Prepared、已退休但 key 仍存在，
以及 protected Archive prefix。它们只证明 metadata 规则，不证明 quorum。
相关 all-targets/features Clippy、私有 Rustdoc、检查器 Ruff/mypy/函数文档通过。

`completed-retirement-native-01/result.json` **通过**，在现有 Prepared 恢复、SQL
故障、实际 child wait/restart 和释放链末尾增加真实 Completed 退休，未重跑不变
的 standalone GC/恢复矩阵。三个 supervisor PID **4163973/4163974/4163975**
最终均实际 wait 0，零具身 Nodes、设备、模型和模拟器。

实际 source owner：`artifact-store:28fc117b7be50d141855fbf441b06acaa8f10af9c0a116d128a9a5eb4ba7c762`。
新 writer：`artifact-store:3d292aba50e344605c5af0d17245b41fc0804a3c86df894711516e218e823389`。
原 **131101 bytes** 的 Completed record 不变，原 owner 的退休 receipt 恰好一次，
重复请求不新增 Domain event，新 CAS 中该 digest 文件仍不存在。

`independent-completed-retirement-verification.json` 在全部进程退出后独立只读核对
实际 Controller/Replica/Root SQL 与文件。原 key 从当前 inputs 移除，原 event
origin 仍在。三个 Root 文件 inode **58589494/58589504/58589507** 的完整 size/hash
正确，独立 stream/log 引用仍活动；原 source 的旧 View 仍是独立责任。两条完整
child reader SQL 记录匹配该 key，其中一条区别于之前的跨 owner HTTP reader；
仅凭 rows 不区分所有读取用途，实际调用链与返回验证共同限定其证据范围。
索引新增 result 与独立复核，现有 157 项。

| 冻结 binary | SHA-256 |
| --- | --- |
| controller-replica-adr0182-01 | `2f15921d88ce5edb5ba08ae4b8b0ab436130647bc8ec977ff68389be3ad4e660` |
| integration-server-adr0182-01 | `fb7ad1d9039919dbd74809248baf8fb274f2e16595dfa7f84fb60fc23b65b5f9` |

原始配置、完整 source identity、HTTP/SQL 与 native waits 留在上述目录。字节保留
不证明当前 use、全局 GC、设备停止或物理连续性；完整跨主机/在线 HA 仍待验收。

## 接续

原 Unknown 的只读对账、parent8/Root4 完整 bundle/升级、online readiness、
Archive/Snapshot/GC 及其它全部非发布阶段继续必需。此包不标 SoftwareComplete。
