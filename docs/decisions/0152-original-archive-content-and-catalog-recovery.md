# ADR-0152：原 Archive 内容与 Catalog 恢复

- 状态：Accepted
- 日期：2026-10-22

## 决策

事件归档必须同时保存原事件前缀、原完整 application checkpoint 及不在该 checkpoint
中的 Catalog/upload/localization/retention 恢复证据。现有 legacy archive 继续拒绝
包含此类证据的 cutoff；显式新合同先在同一原 writer 内冻结完整原投影，归档实际
内容闭包并验证后，再以一次 SQL maintenance transaction 原子保存 Catalog 恢复
投影、归档 manifest 和 history floor。不能先删事件后补投影，不能重新产生原预算。

Catalog 恢复投影保留每份原事件的完整 envelope/payload/sequence，按原顺序与未归档
tail 合并，仅供现有 Catalog/retention/localization 重建；它不是新的 Control authority
或全局 event-sourced reducer。原 event 查询分页继续报告真实 history floor。未知
投影版本、缺 mandatory table/行、改变的 payload/hash 均拒绝恢复；不能视作空 Catalog。
旧 binary 以 shared application wrapper fence 拒绝；旧 archive/recovery profile
不能遗漏该 authority。新引用 selector 只读索引须同时核对原投影与当前 tail。

实际 Artifact owner 在内容读取/复制前持久化原 Archive command/输入/预算/引用，
绑定原 checkpoint/prefix/Catalog digest、tenant/principal 和实际 source/target owner。
实际 archive publication 包含原内容闭包及完整来源；相同 digest 不合并不同 owner。
只有原发布验证与原 SQL compact receipt 可独立对账后才退休 source pin；复制、发布、
SQL 或回包中断保持 Unknown，原 command 不重复副作用或刷新 deadline。归档接收方
对内容持有独立保留责任，source pin 退休不等于 archive 可以删除。

Archive 的显式退休须原 retention policy、当前权限及所有独立消费者证据，tombstone
先于 unlink。GC 同时检查 Catalog/lineage、Snapshot/Archive/online Replica 引用，
缺失 mandatory evidence 即拒绝。引用和副本保留不授予 Memory 内容访问权限。

## 验证

实际 Controller/mTLS/File/CAS/SQL 产生原证据，执行真实维护 API；独立读取原与归档
bytes、SQLite Catalog 投影/retention/history floor，重启恢复同一原事实。覆盖发布、
SQL Commit/回包中断、原命令只读对账、字节缺失、旧 binary/CLI、精确退休和 GC。
单元 parser/协议 fixture 不替代真实 producer；软件停止不作为设备证明。在线多数派、
Raft Snapshot/升级/跨域以及其余 N1–N7 完成条件继续独立验收。
