# ADR-0087：Controller 复制历史锚点压缩

- 状态：Implemented for the accepted metadata profile
- 日期：2026-10-12
- 范围：`core/controller-replication` 应用事件元数据

## Decision

完整 checkpoint 仍是恢复 authority。达到应用历史容量边界时，在同一持久化事务中先确认
checkpoint，再删除旧事件行，并写入 `HistoryAnchor`。锚点记录被压缩前缀的最后 sequence、
前缀 digest、当前 checkpoint digest 和 codec schema。保留事件从 `base_sequence + 1` 连续
开始；checkpoint 可以覆盖没有任何保留事件的完整前缀。

快照、重启和只读检查都验证锚点、checkpoint digest 及保留 tail。旧格式遇到带锚点的快照
时必须拒绝写入。Inactive Controller materialization 使用完整 checkpoint 加可验证 tail，
不假设已经拥有被压缩的旧事件。

## Boundaries

- 锚点压缩限制在线 metadata replay 的空间，不提供外部归档系统或完整历史查询。
- checkpoint 损坏、锚点不连续、digest 不匹配或单批次本身超过上限时 fail-closed。
- Artifact/Memory 内容、Control 资源 authority 和 Runtime reducer 不由复制 store 接管。

## Validation

覆盖显式压缩、空 tail 重启、快照安装、损坏 digest 和原有 OpenRaft storage conformance。
