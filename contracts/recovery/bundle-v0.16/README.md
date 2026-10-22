# Recovery v0.16：源 Artifact 保留释放与回收

显式 `roboguide.recovery-profile/v0.16` 保留此前完整 Memory/Replica/Archive/
Catalog/adoption、原 writer、预算与目录合同，bundle 仍为 v0.4，restore receipt
为 v0.16。wrapper v52/event codec v31 增加独立 `ArtifactUploadRetired`；原
Completed 上传 receipt 不改写，只有当前同租户原主体或 Artifact admin 可以退休。

`POST /v1/artifact-uploads/<id>/retire` 接受闭合
`roboguide.artifact-upload-retirement-command/v0.1`、原 `command_id`、完整
`original_upload_digest` 和 `reason`；body 不接受 tenant、owner、替代 receipt
或权限。原上传与 Catalog/child lineage 独立释放，退休命令不删除 bytes。

v0.16 每个 `artifact` component 增加必填 `storage_schema`，只能声明实际 v3
或 v5。声明必须与原 owner/header 一致。v5 是可写源 CAS 的新显式格式；v4
仍只用于终结的私有 Archive。v5 保留原 owner/inventory/maintenance 并增加
mandatory `reclamation-v1/identity.json` 与 `journal.sqlite3`，旧工具拒绝。

`gc-artifact` 必须指定当前 profile、Artifact component、完整 digest/size、
原 command 与 reason。它取得全部实际 writer，要求包含 CAS 已知的每一个
Controller。当前显式拒绝 Replica/online 权威，不能借离线静止替代多数派。
未归属旧 Catalog、活动 Catalog/lineage、Prepared/未退休 Completed 上传、
未确认的当前 use/import/handoff、原 verifier/goal CAS、Snapshot 与未终结
Archive 均继续保留内容。缺独立原释放证明不自动清理。

原 MI parent/child 声明须实际 policy 已显式 revoked、Mission 已终结、全部 switch
Applied/Cancelled；Completed 还要求 exact 原 Control goal contract/receipt、独立来源
及原 kernel 完成预算。不可由终态字符串或到期替代。原 Catalog 退休 digest 包含完整
该修订证据；符合条件的历史 digest 不再单独阻止已释放内容回收。

v5 GC 原命令、实际 inode/hash/size、完整原闭包、writer/PID/kernel 与首次预算
和不可逆 tombstone 先于 unlink；同一 owner 的该 digest 永久拒绝读取、verify
和 finalize。失败保持 Unknown，相同 ID 只读观察；新显式命令可以验证剩余
bytes 再执行自己的 unlink，不能重写较早 Unknown 或刷新它的预算。

`retire-artifact-snapshot` 对升级/恢复后的旧未发布 Snapshot pin 另外要求完整
原 profile、operation/intent digest。原 profile digest 必须匹配；全部实际原、新
writer 被持有且原 destination 未发布后，才保存原 v0.2 abort receipt。旧 intent/
预算及历史源不改写，复制或 unlink 均为零；原故障域无法排空则继续保留 pin。

冷恢复核对所有 mandatory 原 journal/head/rows/tombstones/retirement/原 bytes。
已释放历史不要求已删内容；活动 Catalog、上传或验证证据与 tombstone 冲突时拒绝。
恢复保留原命令与原 Unknown，不启动服务、Node、模型、模拟器或设备。

实际门禁为 `tools/quality/check_artifact_reclamation_processes.py`，其输入来自
实际完成的 legacy adoption 或 MI child lineage 进程证据。实际通过范围单独写入验收记录；此合同
文本不是在线 HA、设备停止或全部非发布整改完成的证明。
