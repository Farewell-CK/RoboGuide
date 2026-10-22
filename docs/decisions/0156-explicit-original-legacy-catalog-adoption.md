# ADR-0156：原未归属 Catalog 的显式保留责任接管

- 状态：Accepted
- 日期：2026-10-22

## 决策

旧 Catalog 存在不等于已记录实际原 Artifact owner、发布 tenant/principal 或 lineage。
新增真实 tenant Artifact admin 的显式 adoption；只作用于已经存在且尚无保留记录的
精确 selector。请求固定原 publication identity/digest 与原 command；实际 source
envelope、完整 generic/typed metadata、实际当前 CAS owner/bytes 和已有 typed parent
由原 writer 读取，不由请求构造新的 manifest 或设备/provider 事实。

接管仅建立当前保留责任，不声称恢复缺失的历史发布者/tenant。新 receipt 明确保留
原 publication 与未知历史归属，记录实际当前稳定 admin、TLS leaf、完整 grant/registry
证据、原 command 和首次接纳时间。没有额外 read/use/import/Group 权限；所有后续
消费继续由原 policy/Control/Node 当前接收和预算门禁管理。不得修改原 manifest/scope，
typed parent 须已由相同当前 tenant 保留，缺 parent 或原 bytes 拒绝。
候选发现与接管必须通过当前 Controller 的完整 tenant/Memory Discover policy；
Artifact admin 角色本身不越过该策略。Commit 再检查同一实际 TLS 主体与完整策略。

receipt 与实际 Catalog/lineage pins 同一原 application writer/SQL transaction 提交；
CAS 验证 I/O 后在 Commit 前重新验证当前原调用者。相同原 command/输入只观察原 receipt，
不同输入、重复绑定另一个 selector 或已退休记录不能替换。SQL 故障不发布新引用；
原 Catalog 和 unknown attribution 始终保留，不改写历史。

新增完整 `ArtifactCatalogLegacyAdopted` 原证据事件，event codec v30/shared wrapper v51
fence 旧 writer；内层 Runtime、Node/Memory 与实际 CAS 格式不变。原 Catalog archive
必须保存该完整 envelope 并重放；显式 recovery v0.15 验证原 publication、当前接管
receipt、完整依赖与同 owner bytes。旧 profile 不得忽略新 authority。v50 历史保留
原字节，不把旧 publication 重标为已验证的历史 tenant/publisher。

实际门禁须先由旧显式软件进程产生真实未归属 publication，再以实际 mTLS/SQL/CAS
验证接管、原重试、错误主体、SQL 回滚、重启与冷恢复。合成旧日志不能冒充实际旧生产者；
0 物理设备/外部模型/模拟器。此项与源 Artifact 全引用 GC、Raft/online、N2–N7 分开验收。
