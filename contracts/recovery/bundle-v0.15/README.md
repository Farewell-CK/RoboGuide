# Recovery v0.15：原未归属 Catalog 的显式接管

`roboguide.recovery-profile/v0.15` 保留 v0.14 的完整 owner/consumer/writer、Memory、
Replica、Archive cleanup、私有接收终态 storage v4 与 GC history；bundle 为 v0.4，
restore receipt 为 v0.15。普通 Artifact 仍为 storage v3，Node/Memory 合同不变。

shared Controller wrapper v51 / event codec v30 增加完整
`ArtifactCatalogLegacyAdopted` 证据。它必须与同一原事务的 Catalog/lineage acquisition
匹配，并固定实际原 publication envelope、完整原 raw payload、当前接管 admin/TLS/
grant 与原 command。历史 publisher/tenant 仍为 null，不由当前身份反推。

实际入口是 tenant Artifact admin 的 exact selector：

- `GET /v1/artifact-references/<memory>/<revision>/legacy` 读取原 publication digest。
- `POST /v1/artifact-references/<memory>/<revision>/adopt` 提交闭合
  `roboguide.legacy-catalog-adoption-command/v0.1`、原 `command_id` 和
  `original_publication_digest`。

body 不接受新 manifest、owner、tenant、provider、scope 或授权字段。实际 bytes
和 typed-parent 的原 retention 都须验证；最后 Commit 再验当前主体。接管只建立
当前保留责任，不增加 read/import/use/Group 权限，不改写原 publication。

原 command 精确重试保留原 receipt/time；不同输入/selector 不复用。SQL 失败不
留下 reference 或 receipt，Unknown 不变成新 owner。新的 reference 若发生在旧
publication 之后，必须有完整 original adoption receipt；缺失时不能从 CAS 或当前
配置补造。Archive 保存并重放完整原 envelope，不能只留下最后 head。

v0.15 冷恢复核对原 source envelope 的 sequence/event/time/cause/schema/raw bytes、
新 receipt、同 owner CAS 和完整 lineage。早期 profile 拒绝 wrapper v51；旧 binary
拒绝 v51/v30。unit parser fixtures 的身份/租约不是部署事实，实际旧 v50 producer
与 mTLS/SQL/CAS 升级证明单独保存。在线多数派、全引用 GC 与剩余 N1–N7 继续必需。
