# ADR-0183：原恢复命令的只读事实对账

- 状态：Accepted，已实施并通过本包实际进程验收
- 日期：2026-10-24
- 前置：ADR-0177、ADR-0180、ADR-0181、ADR-0182

原 recovery completion SQL 不确定时，独立 replacement 可能已正常 Completed，
原 source 也可能之后明确 Abandoned。仅返回冻结的原 recovery record 无法表达
这些独立事实；不得因它们补造原完成、重开 copy 或续预算。

新增精确只读 `GET /v1/artifact-uploads/<upload>/recoveries/<command>/assessment`，
使用独立 `roboguide.artifact-upload-recovery-assessment/v0.1`。原 observation
路径/结构保持，包括合法 command ID `assessment`。同一个原 writer 下取得原
record、当前 source 和 replacement，核对完整不可变 source/原 admission 归属。
replacement 缺失明确为 unavailable，不能解释为未执行。
已有 source/replacement 退休回执也分别返回，核对其完整对应 upload；历史 Completed
不意味着当前保留或可用性。记录存在只标为 present，不把它写成 bytes available。

保留原 uploader/tenant、实际 TLS/grant、epoch/quorum 和 committed-local 范围。
写禁用下沿用明确的 audit_persisted=false；restart/Catalog 不确定性仍需 fresh
complete-quorum checkpoint 与本地精确匹配。普通业务端口没有该例外。

原 recovery acceptance 与当前 source/replacement 分别报告，repeat_allowed 恒为
false。它不创建 read/import/Execute、恢复写权限、变更原 completion 或推断 Root
reader 的操作绑定；Root 独立证据与完整 parent8/Root4 恢复仍为后续必需项。只读
组合不改变持久/Node 合同，也不证明设备停止或全局完成。

验收覆盖 completion SQL 故障下的 Unknown + replacement Completed、实际重启、
source Abandoned 后的同一原 Unknown、无新事件/复制、缺 replacement、跨 tenant
拒绝和原路径兼容。全部证据限定为真实软件/SQL/TLS/文件，不增加设备事实。

结果见[实际验收](../development/acceptance/2026-10-24-original-recovery-assessment.md)。
