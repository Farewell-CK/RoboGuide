# ADR-0177：Prepared 上传的显式恢复与独立新上传

- 状态：Accepted，已实施并通过本包实际进程验收
- 日期：2026-10-24
- 前置：[ADR-0176](0176-owned-controller-qualified-content-reads.md)

原 `Prepared` 仅证明原 owner 的 staging bytes，副本中的 qualified bytes 不能证明
原 CAS 已 finalized。新 Controller 必须保留原 upload、owner、预算及 Unknown；不得
改写原 owner 标记、复用同名上传或把旧 Prepared 直接记为 Completed。

显式 `POST /v1/artifact-uploads/<original>/recover` 使用独立 command ID、原完整
record digest 和有界预算。仅原 tenant 的原 stable uploader 可以发起。新意图先于
任何新 staging/read，冻结原完整 Prepared/key、实际目的 CAS writer inode、当前原
Controller incarnation、实际 kernel budget、真实 TLS/grant、唯一 reader ID 与独立新 Started 上传。
同 command 重试仅返回原证据，不复制、不续期；中断保持未确认。不同命令不会复用
旧 replacement ID，原未确认责任保留。

parent8 私有读取新增 PreparedRecovery 用途：必须从完整当前 metadata 中找到同一
已提交意图，复核原 process、actual peer、原预算和 qualified source。每个 bounded
chunk 均核对当前原权限与预算。复制使用现有 upload count/byte quota；真实完整
reader close、hash/fsync、正常 Prepared→CAS finalize→Completed 和原 writer 证明
依次先于恢复完成。新 Completed 属于新 owner；原 Prepared 不变。两份 qualified
引用独立保留，恢复完成不释放其他 Catalog/Archive/reader/原上传责任。

新增 Domain recovery 事件使用 codec v32 / Controller wrapper v53；v52 writer 拒绝
新 wrapper，即使没有 Domain event。旧历史原样迁移，旧网络/Node/Memory 合同不
获得隐含恢复能力。Catalog/Archive replay 保留恢复意图和原证据，非法原 record、
替换源/目的 owner、跨主体、过期预算、缺完整回执和 SQL 失败均拒绝。

验收需要真实三副本、原 Controller HTTP 上传在 Prepared 后实际退出、新 leader
恢复、独立原/新 owner 的 SQL 与实际 CAS 核对、只读重试及失败后不重做。软件
读取和进程退出不证明设备停止、当前 Memory use、跨主机可用性或 SoftwareComplete。


请求体使用闭合字段：

```json
{
  "schema": "roboguide.artifact-upload-recovery-command/v0.1",
  "command_id": "caller-owned-unique-command",
  "original_upload_digest": "sha256:<canonical-complete-original-record-hash>",
  "budget_ms": 120000
}
```

`budget_ms` 接受 100–300000，包含首次准入、排队、读取、正常上传及提交；同 command
改变预算或原 record 被拒绝。调用者不选择 source owner、destination owner、PID、
reader ID 或新 upload 的内容证据。首次请求只有实际正常上传及 Root receipt 核对后
返回 `completed`；未持久化的完成不补造。相同命令的查询返回原 record / `replayed`，
未确认 `completion: null` 保持 `unknown`，不能据此重新复制或续期。

显式 cold recovery v0.18 继承原 v0.17 的每种声明及 storage writer fence，允许新
wrapper 的无恢复操作历史。它仍拒绝包含新 recovery 事件的 bundle；原 parent8 /
Root4 的完整在线/静止恢复和未确认操作只读对账是接续必需项，不能由版本号替代。

全部 standalone source GC、原 Snapshot 退休、Controller 检查与 writer inventory
必须透传实际选择的 profile。v0.18 的 Controller 明确声明实际 legacy 或 v1；
检查器读取实际 header 仅生成声明，完整 SQL/lease/bytes 仍由 CLI 验证。不能在内层
换回旧 v0.16，或以放宽旧 profile 接纳 v53 代替接线。旧 intent/receipt/profile
digest、时间预算和扫描上限保持；显式 v0.18 仍拒绝新 recovery 事件和 parent8/Root4。

实际两存活 voter 已提交元数据而旧固定 5 秒 callback 截断的反例，要求显式 content
Commit 等待使用首次原 kernel 预算的实际剩余值，包含已花费的排队/复制时间。它仍只
发送一次 Raft command；超出原剩余预算保持 Unknown，不刷新 deadline，不改变原
legacy metadata profile 的等待、Raft heartbeat/election 或 strict-majority 规则。

原 upload 只读 observation 最多展开 16 个 recovery record，并返回总数、完整
command ID 列表和 `recovery_history_complete`，不把大历史无界展开成 HTTP JSON。
同 command 的原 POST 只读重试仍返回该原 command 的完整记录。

本包结果与证据边界见[实际验收](../development/acceptance/2026-10-24-prepared-recovery-and-runtime-latency.md)；接续必需项不因本 ADR 状态关闭。
