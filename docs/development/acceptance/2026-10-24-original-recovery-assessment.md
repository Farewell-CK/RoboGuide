# 原恢复命令的只读事实对账验收

2026-10-24，对应 [ADR-0183](../../decisions/0183-original-upload-recovery-assessment.md)，
继续唯一分支 `codex/nonrelease-remediation-closure`，产品前置 `0688373`。

精确 `/v1/artifact-uploads/<upload>/recoveries/<command>/assessment` 将原 acceptance、
当前 source/replacement record 和对应退休回执放在同一 writer snapshot 中。
原 admission/不可变字段必须匹配；缺 replacement 明确 unavailable，存在仅为
present。独立 Completed、退休、source Abandoned 都不补造原 recovery completion。
原 record、预算及重试禁止保持。它不观察或证明 Root reader 的 operation binding。

实际当前 uploader/tenant/TLS/epoch/quorum 与原 write/restart/Catalog checkpoint
fence 保持。故障时明确 audit_persisted=false 和 committed-local 范围，业务 write/
bytes 入口仍被拒绝。原 observation 路径与 command ID `assessment` 兼容。

## 原始验证

证据根目录：`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。
相关 **277 integration-server** 回归通过；增加退休字段后，相关真实 HTTP contract
针对性复测通过，覆盖缺记录、Completed + Abandoned 仍 Unknown、独立退休、跨
tenant/错误 replacement 归属拒绝、旧路径兼容与故障 fence。该 cache fixture
不制造实际进程、内容或 quorum 证明。Clippy、私有 Rustdoc、Python Ruff/mypy/
函数文档通过；完整维护范围 mypy 366 files 亦通过。

`recovery-assessment-native-01/result.json` **通过**：实际三副本/Controller、原
Prepared 恢复、正常 replacement Completed 后的恢复完成 SQL 故障、写禁用下 GET、
实际 child wait/restart、source 放弃、Completed 退休。新增 GET 没有 Domain event、
copy 或新的恢复完成记录；同一原 120000 ms budget 未更改。

实际 member 337 上，故障时 source 为 Prepared、replacement 为 Completed，
原 recovery acceptance 为 Unknown。重启及 DELETE 后 source 为 Abandoned，
同一 replacement 仍 Completed，原 Unknown 完全相同。三个 supervisor
PID **3846/3847/3848** 最终实际 wait 0；零具身 Nodes、设备、模型、模拟器。

`independent-assessment-verification.json` 在所有进程退出后只读核对实际 SQL：
该原 command 只有一条 pending intent、completion 仍 null，当前 source 与
replacement 精确匹配返回的两个独立 records。两份 assessment 原样保留，不能
仅凭 JSON passed 或独立 Completed 推断原操作被接受。

| 冻结 binary | SHA-256 |
| --- | --- |
| integration-server-adr0183-01 | `0119c8104b29e09af79875e7381c34b6c0017eb84d2ac669a5f80015d845f942` |
| controller-replica-adr0182-01（未改协议的原 supervisor） | `2f15921d88ce5edb5ba08ae4b8b0ab436130647bc8ec977ff68389be3ad4e660` |

原 source identity、配置、HTTP 和 native waits 留在原目录。索引加上本次结果/
独立复核及私有 recipient 的真实 v49/当前 v53 两种检查，现有 161 项。

## 边界

本包闭合本地已提交 records 的只读事实对账，不恢复新 copy/use/Execute 权限，
不改变原 Unknown。完整 Root reader 来源绑定、parent8/Root4 bundle/升级、
online readiness/GC 和其余全部非发布阶段仍必需，尚非 SoftwareComplete。
