# 原未归属 Catalog 显式接管验收

唯一开发分支为 `codex/nonrelease-remediation-closure`，接续 `f739dbe`，实现
[ADR-0156](../../decisions/0156-explicit-original-legacy-catalog-adoption.md)与
[recovery v0.15](../../../contracts/recovery/bundle-v0.15/README.md)。前一提交的
[完整 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39805016096)和
[文档 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39805011072)成功，
保留 `702b799` 原 MI Broken pipe 失败证据，不将其改写为成功。

## 责任与原证据

新入口只对现存且未归属的 exact selector，由当前实际 tenant Artifact admin
提交固定原 publication digest 和原 command。原 source envelope 的 sequence、
ID、time、cause、codec 和完整 raw payload 由同一原 SQL writer 读取，当前 actual
CAS owner/bytes 与已有 typed-parent reference 均验证。原 envelope 扫描在分配候选
前受 16384 events/64 MiB 限制；内容验证后、Commit 前重复当前实际授权检查。

完整当前稳定 admin/TLS leaf/grant/registry、原 command、原 source 和实际新
Catalog/lineage pins 同一事务提交。历史 publisher/tenant 均为 null；不是用当前
身份补写旧身份，不改原 manifest/scope，也不新增 read/use/import/Group 权限。
相同命令重试只观察原 receipt，当前证书轮换仍保留稳定主体；不同主体/tenant/input/
selector 不借用原命令，已有/已退休 reference 不被重写。

shared wrapper v51 / event codec v30 fence 旧 writer；v50 的原进程、配额、Memory、
Archive journals 与原 embedded checkpoint/budget 保留。新 acquisition 出现在旧
publication 之后时，必须有独立同事务 `ArtifactCatalogLegacyAdopted`；缺 receipt
不能依赖 CAS 现存 bytes 补造。重放核对 exact raw string，不能用语义相同的新 JSON
代替原始字符串。Archive 同样保留完整原 adoption envelope。显式 v0.15 延续 v0.14
私有接收 retirement/GC、完整各原 writer/consumer/CAS/目录与旧操作 budget。

## 实际门禁与失败记录

证据根为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`。

| 原记录 | 实际范围 |
| --- | --- |
| `adopt-old-source-02` | 原 v50 Controller 使用显式 legacy CAS profile，两个正式注册的软件 Node、真实 CPU 文件与独立 verifier；产生真实未归属 publication 并完整退出，0 物理设备/外部模型/模拟器 |
| `adoption-frozen-final` | 当前 v51 使用原 SQL/CAS/PKI，实际授权当前 Artifact admin；viewer 拒绝、原 digest 冲突、实际 SQL receipt 故障后 no reference、原重启/精确重试；原 publication 字节未变，当前 receipt 的历史身份保持 null |
| 同项 cold gate | 实际 v0.15 CLI backup/verify/restore/幂等 retry，v0.14 profile 拒绝，真实旧 v50 binary 拒绝 canonical 恢复 SQL；未启动新 Node/设备/模型 |
| `adopt-mi-final` | 当前 v51 与已修复 provider，原 Request/actual worker interruption/Review、两个 Task 独立 import/use/CPU 与原目标组合，实际单 network worker |
| `adopt-mi-cold-02` | 上述实际 MI child lineage 的完整原 SQL/CAS 冷恢复，旧 v50 使用真实原 Artifact owner 后拒绝新 wrapper |
| `adopt-archive-final` / `adopt-arc-gc` | 当前 v51 的原 copy/abort/升级/冷恢复，以及独立私有接收端的 intent 故障、partial unlink、只读原重试与冷恢复 |
| `adoption-tenant-final-02` | 最终授权修复构建；实际外租户 admin 的 GET/POST 均拒绝，同租户显式 Discover-only 授权；SQL rollback/原重启/精确重试/完整原 adoption Archive 再重启及 v0.15 冷恢复组合通过 |

旧 producer/旧拒绝 binary 的 SHA-256 为
`1c36eb8b167e5439593cb8152cfbc9a5299e725e8b2c0443bca367ff3eeef49a`（wrapper v50）。
`adoption-frozen-final`/`adopt-mi-final` 的实际当前 Controller 为
`883e9fa2d3a729368ccec93e92760f43a4560baa439ff6c9ae2b497db6a9f77e`（wrapper v51），
Node binary 仍为 `7103a3ba52c4ae9829b5f67e214d936cb4826e0a5e515d78249570a33c168c1e`。
最终 `adoption-tenant-final-02` Controller SHA-256 为
`5446d048a89a29ad4e5476df2849f3f1e885c795bbfd7254630f0e85030412f2`。
各轮 source/config/binary、原 SQL/argv/stdout/stderr 单独保留；不同构建不合并身份。
原完整 P1 evidence index 只加入实际通过的 result，所有失败仍留存。

`adopt-legacy-source` 首轮实际 Node 上传 content 观察到 404，未在原 20 秒观察内发布，
因此不算旧 producer 成功；新目录 `adopt-old-source-02` 以相同预算完成。原故障待由
完整 listener/上传恢复矩阵继续核实，未放宽产品 TTL。`adoption-native-02/03` 已完成
接管/cold，但旧拒绝检查直接复制主 SQLite，遗漏 WAL 后读到了更早原状态，先因原
Node lifecycle profile 拒绝；不能计为 v51 downgrade proof。改用实际 CLI canonical
恢复的完整 SQL 后，原 `v51`/`v30` 拒绝门禁通过；未修改源 authority 来绕过身份检查。

`adoption-arc-final` 首次组合也因检查器仅复制原主 SQLite、遗漏 WAL 而读到较早
Running；独立核对原 Mission 实际为 Completed。检查器改为取得原 Controller/CAS
writer 租约后，用 SQLite backup API 复制一致视图，保留原完整 wait 记录；
`adoption-arc-02` 和最终 tenant gate 才计入完整 Archive 组合通过。

`adoption-tenant-counterexample` 实际证明早期包装层默认授权跳过了 Controller 的
完整 Memory policy，外租户 admin 可以读取候选；该失败保留。最终入口与 Commit
均调用实际 Controller 授权，外租户 GET/POST 都返回 403。`adoption-tenant-final`
又正确拒绝尚无 Discover 权限的同租户 admin；最终通过现有管理 API 显式授予
Discover，未授予 Read/Import，才完成接管。接管命令本身从不补授内容权限。

CI 另由当前 binary 的显式 legacy storage profile 产生未归属 publication，再走
同一实际 adoption gate。它证明 legacy profile 的兼容边界，不伪称跑了旧版 binary；
旧 v50 producer 与旧 binary 证明来自上表的独立冻结路径。

## 检查与继续工作

新增单元合同验证最后实际 permission witness 丢失时 SQL/reference/receipt 共同回滚、
原 retry 与缺 receipt 恢复拒绝；synthetic parser fixtures 验证重算 digest 不能替换
原 envelope/raw bytes 或捏造历史 tenant/publisher。这些 synthetic 身份不进入部署。
最终 Rust 全量 **976 passed、2 ignored**，Clippy/private rustdoc 均通过；全量
Python JUnit 为 **3086 passed、0 skipped**，含实际 Python 3.9 标准库桥接，未启动
Habitat/模型。Recovery **109 passed**；strict mypy **334 files**、Ruff/format 和
函数文档检查通过。全量 Python 记录早于最后 Rust 授权修复，两者原构建/日志分别保留。

此项关闭旧 Catalog 的显式当前保留责任接管路径；完整源 Artifact GC、其它 producer
覆盖与释放、Raft Snapshot/online、typed-map 总链路及全部 N2–N7 继续必需。未提供
设备停止、硬实时、跨主机多数派内容 ACK 或 SoftwareComplete 证明。
