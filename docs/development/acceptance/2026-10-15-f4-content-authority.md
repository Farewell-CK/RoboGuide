# F4 当前内容门禁软件验收

状态：当前切片软件验收通过；F4 整体仍在实施。承接 `0af2cc8` 的原 provider 连续性切片，
不能据此关闭完整 Group 授权、Memory 生命周期或后续 F5–F10。

## 当前调用链

全局 recovery-admin → command/expected revision/稳定主体 receipt → Control 的完整 Memory
policy / Artifact certificate registry / 原 history → 当前 tenant、manifest digest、provider、
Group membership generation → 原 import intent → 发送与内容访问门禁。绑定 A → B → A 不能
复活旧 generation；旧记录缺少成员版本保持 unavailable。Import 冻结原 tenant/provider/
policy/member 与提交者，重试、恢复或换证书不更新旧依据。

Artifact 核对实际 TLS leaf/notAfter 和当前 Control 授权。Node grant 还需正式接纳的当前
session/lease、identity 与必需的 attestation；仅登记证书不能制造 Node。Catalog mutation、
上传 byte write 和 CAS finalize 在 writer ordering 下重查权限，blob 逐块重查。目录 discovery
独立于内容消费。Header、CAS 哈希、新 selector 和 typed-map alias 不授予权限或改变 scope。

上传在实际完整 bytes 的 hash/size/fsync 后才写 Prepared，CAS 可验证后写 Completed。最终
SQL 失败保留 Prepared；exact finalize retry 只核对原 CAS，保留原 admission/deadline/
preparation。Started 不证明存在完整内容。跨 scope parent/child handoff 现在必须经过 Control
的显式 `Handoff` principal grant：owner/provider/bytes/schema/media 不得改变，scope 只能收窄；
Global source 才能进入当前非 Released target Group。没有该 grant、目标 Group 或完整来源
一致性时仍拒绝。实际跨 Group stop barrier/物理 handoff 仍是独立 gate。上传归属事件在完整
snapshot/archive/replay 落地前不能删除。

wrapper v32、inner v21、upload codec v23 保持回退 fence；Node journal 仍为 v7。原 Local
EAIOS 工作流 context 不变；Node 从原 continuity identity 为 HTTP 附带 opaque import ID，
服务端自行核对其实际权限；这个 ID 不是 bearer credential。

## 实际进程证据

当前 Controller SHA-256：
`56c95651e58909ef6bb9712e6d46277b75eb74c49b4bfef64e14d904d35606c7`。
当前 Node SHA-256：
`351b2c7d8ae55861121f39eb799ccc24e06bb1119789f6ea661861db8595dce0`。

```bash
uv run python tools/quality/check_artifact_authority_processes.py --skip-build \
  --output /tmp/roboguide-f4-artifact-authority-20261015-reviewed
uv run python tools/quality/check_memory_processes.py \
  --output-directory /tmp/roboguide-f4-memory-processes-20261015-content-gate \
  --old-node-binary /tmp/roboguide-node-f3-journal-v6-reference
uv run python tools/quality/check_controller_replication_processes.py --skip-build --tls \
  --output /tmp/roboguide-f4-replica-check-20261015-content-gate
```

三项 passed=true。Artifact 检查注册 0 个 Node、0 台设备，实际传输源码文件 25245 bytes，
SHA-256 `b4c1b655cee52659197ea57df74b224486fe2c4914c024bff552ce3cd08ba227`。
撤销拦住正在接收 body 的原请求，未形成 Prepared/Completed；新 leaf 经当前 registry 获准后
延续同一主体的另一原上传。跨主体查询/伪造 role header 被拒绝。重启后返回同一 receipt，
4 个 upload transition 没有重复。`...-a` 保留此前代码布局的独立通过记录，未覆盖原件。

Memory 回归为 1 个实际文件软件 Node、0 台设备、1 个实际消费者与 1 个独立复算进程；
backend 仅一条 import row，duplicate imports=0，query 后原 row 仍为 pending。旧 v6 Node
拒绝写 legacy view，退出码 1；没有重复 Execute。配置 digest 为
`sha256:f5ec21ed397d88df1e50676a8b709b006f59c56570c63b7876437e6203023629`。
该轮重启/连接关闭阶段保留一次本地 HTTP fixture 的 BrokenPipe traceback；真实收据、文件
和独立复算通过，没有删除该异常或将其当作设备证据。

相同 Controller 的静态三成员实际 mTLS 检查为 wrapper v32：minority、旧 epoch route fence、
Leader death 保留 Unknown/ownership/policy/journals、34 行元数据前缀汇合、单机降级拒绝和
majority loss fence 均通过。LocalFixture 是合成协议矩阵，不证明物理执行连续性。

## 自动检查与剩余项

完整 Rust workspace 802 passed、2 ignored。之后 context 保持与 session 门禁修正分别重跑
Node 118、Control 153/Application 213；workspace Clippy、Rustdoc、fmt 通过。新增检查覆盖
哈希 alias、空 upload 借已有 CAS、SQL 最终故障、scope 逃逸、ABA 成员重放、撤销保留
ownership、实际证书时间、恢复 session 清空、租户管理员越界和旧 schema。旧测试补入显式
授权，没有放宽生产 gate。两项扩展 ownership/replay 检查仍须在 F10 执行。

Python 2463 项、strict mypy 198 sources、Ruff/format 207 files、函数文档通过。两份授权 schema
结构有效，真实 revision-2 Artifact registry 通过 schema 校验。

仍须实现并验收 Node Group grant/接收预算/撤销 ACK、pre-Execute Memory prerequisite、当前
availability、运行中撤销 stop/handoff、跨 Group stop barrier/physical handoff、active-map、
tombstone/完整引用图/GC 和完整空间接线。parent/child exchange 的 Control scope/owner/bytes
门禁已接入并由 Control 回归覆盖。后续[实际点云计算](2026-10-15-f4-spatial-files.md)与
[Operator 事务内身份重查](2026-10-15-f4-operator-transactions.md)已有单独验收，不改写本轮历史。
旧 Group 的显式可信迁移、typed-map 完整远端 provider/session 接线、HA 内容恢复及索引/锁性能仍须在 F4–F10
闭环。真实停止、硬件身份、硬实时和物理连续性保持 HardwareEvidencePending；整体尚未
SoftwareComplete。
