# ADR-0149：原 Catalog 发布与派生 lineage 的内容引用

- 状态：Accepted
- 日期：2026-10-22

## 决策

Controller Artifact CAS 与各 Local Memory provider CAS 保留各自的实际 owner。
显式 retained Artifact 部署的真实 Catalog 发布，在发布的同一 SQL authority transaction
内写入完整内容引用。记录绑定原 tenant/principal、权威 digest、selector、generation、
store owner、semantic provider、原 manifest digest 与实际 bytes digest/size。写入原
intent/引用先于该发布获准引出的任何导入。副本 placement 不能替代原 CAS 保留责任。
typed map 另固定完整原 Spatial manifest digest，不能用 discovery projection 的 digest
替代 frame/anchor/format/producer provenance。

原 whole-Group handoff 和 MI revision 发布 child 时，独立固定原 parent 的 lineage
dependency；typed map 的原 parent revision 同样保留。每份 child 独占自己的保留记录，
不能因父 Catalog 退休或另一个 child 完成而合并释放。发布重试沿用原记录和时间，不从
新证书或重启制造新预算。原 metadata、released 历史仍可审计，不据此重新 acquire。

退休只接受精确原记录的冻结命令与当前授权。所有实际应用 use/import 必须已取得原
不可逆 revoke 证明；未完成、Unknown、来源不全拒绝。退休阻止原 selector 后续发布与
新准备/导入，保留历史语义权限。GC 另须满足所有独立引用，不能凭退休字符串直接删 bytes。
旧未归属 Catalog 不自动赋予 tenant 或补写权限，保持未核对 fence。

shared wrapper v48 和 event codec v29 fence 不理解新 authority 的旧 Controller；正常
v47 迁移保留原 quota、MI、Memory、stop 与预算。Node 合同与原 journal 不改变。
Quiescent recovery v0.10 必须检查引用的实际 owner 与同一 bundle 的原 CAS bytes；旧
profile 不解释 v48 的新引用权威，当前 verifier 拒绝用其验证该 authority。完整
Archive/Snapshot/online 内容闭包继续独立实施，不能由 Catalog 记录推定完成。

## 验证

实际 publish/child 命令须在原 TLS、provider/Node admission 和 SQL 链路触发，独立查询
events 与 CAS。注入 SQL 拒绝、响应丢失、重启、精确退休/重复退休，检查无悬空发布、
无刷新预算与无复活；旧记录和各 owner 相同 digest 的责任保持分离。测试进程只证明
软件路径，不创建硬件、物理停止、跨主机复制或整体 SoftwareComplete 事实。
