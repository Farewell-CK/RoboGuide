# ADR-0125：Task 当前接收上下文独占 import 身份

日期：2026-10-20。状态：Accepted，实施中。

ADR-0108 的后续 Task 验收在当前组合中暴露错误：Memory preparation 按 Node/provider/
manifest 找任意 Completed import，导致下一 Task 的 use grant 携带上一 Task 的原
`execution_id`/`preparation_id`。Local EAIOS 正确拒绝，后续 Task 保持 hold；不能放宽
backend 的原上下文校验，也不能把 Imported 字节位置当作当前 Read 权限。

应用必须从当前原 preparation ID、selector 和 consumer provider 确定 import ID。
同一原 Task/attempt 的重复或恢复仍使用同一 ID、原预算与只读 reconciliation；不同
Task/attempt 取得自己的当前 Prepare/Control 授权和新 import intent。新 Task 的合法
接收可在 backend 验证已存在的相同 CAS bytes，但不改写旧 import、owner、scope、生产
provenance、旧 Unknown 或旧 receiver/provider revoke 证明。

admit_inputs 在接受任何已有 receipt 前比较完整原 request，包括 Group/Task/Role/attempt、
Node/provider、原 preparation、完整 manifest/typed map 和原 deadline。重复传递不会替换
身份；其他上下文的 Completed 不可直接复用。旧接收方完整退役、当前 membership、policy、
Node session、当前 use grant 和实际 provider 预算仍是后续 Execute 的必需门禁。

协议与 checkpoint DTO 保持原义；旧活动 preparation 在既有恢复门禁中撤销，历史原记录
不重写。需要实际双 Node/CAS/SQL 故障/交接/新 Task 独立 import 与文件复算回归。没有
设备或物理连续性结论，完整 F4/F8 和其余非发布要求继续执行。
