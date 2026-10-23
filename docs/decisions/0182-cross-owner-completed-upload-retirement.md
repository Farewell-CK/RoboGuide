# ADR-0182：跨 owner Completed 上传退休

- 状态：Accepted，已实施并通过本包实际进程验收
- 日期：2026-10-24
- 前置：ADR-0157、ADR-0176、ADR-0181

已有 Completed retirement 仍要求当前 CAS owner 等于原 owner，并只验证当前 CAS
文件。新 leader 的 CAS B 不能因此退休原 A 上传。它不能把 receipt 的原 owner
改成 B，也不能以同 digest 的另一份内容替代原 source。

显式 replica-content composition 从实际当前完整 quorum View 的 protected Catalog
前缀与 tail 定位原完整 Completed record、精确 qualified origin 和未退休状态，
沿用原扫描上限。实际 RetainedIntegrity reader 验证该原 source 的完整 bytes 和
Root close，丢弃返回内容，不发布到 B CAS，不授予应用 read/import/use。提交前在
原 writer 中再检查相同当前原来源。每个接收副本继续核对 receipt 的原 namespace。

当前 tenant/uploader 或同 tenant admin、实际 TLS/grant、原 record digest、唯一
command 和原 receipt 保持。只解除该上传的活动引用；原 Completed、Catalog/child、
Root Log/Snapshot/reader 等独立责任不改变，不 unlink。重复命令只观察原 receipt。
standalone 路径继续验证自己的实际 CAS；没有 replica port 不能借用 foreign proof。

沿用原 retirement v0.1、codec v31、wrapper v53 和现有内部完整性读取合同，没有
Node、物理或新的持久 authority。验收须实际原 owner 上传、source 退出、新 leader
经 Root 验证/退休、原 history/owner/bytes/pins 核对与只读重试；合成反例不替代它。

结果见[实际验收](../development/acceptance/2026-10-24-cross-owner-completed-retirement.md)。
