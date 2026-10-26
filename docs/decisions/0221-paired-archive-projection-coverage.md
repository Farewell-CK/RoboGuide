# ADR-0221: Verify retired projections through their exact original Archive

- Status: Accepted
- Date: 2026-10-26

## Context

完整双域 HA 已通过实际正常执行、在线 private Archive、独立进程退出/换主、真实证书
Stage/Retire、分区、原 Unknown、只读停止确认和真实 leaf pin 拒绝。随后的完整静止
备份拒绝旧 child projection：原事件已经转入父副本的 private Archive，但恢复工具仅在
protected Catalog 与当前 tail 查找全部本地旧事件。实际 SQL 比较显示缺失序号均落在
原 Archive 区间，已找到的序号没有不同值。不能把缺失直接当成功，也不能丢掉旧 child。

## Decision

`replica_content_bundle.projection_authority` 保持原 exact parent 与 Controller authority
检查。只有本地序号未在原 parent 的 protected/tail 中找到时，才读取覆盖这些序号的
已完成原 Archive；Root 必须是组件图中与该 parent 唯一配对的同一成员 Root。
source-owner/digest/byte-size 决定实际 CAS 路径，使用现有完整 package verifier 逐字节
核对 SHA、长度、manifest、checkpoint、原 event segment 与 Catalog。

原 protected/tail 与 Archive 以 exact envelope 合并：相同序号不同值立即拒绝，不能
覆盖当前父事件。保留原 32768 个 event 扫描、64 MiB 唯一 event evidence 与已有 128 MiB
单 package 上限，并逐步核对实际 operation budget。其他 owner/member/domain 的同样
数字 ID 或相同内容声明均不可替代实际配对包。没有完成回执、内容缺失、损坏、内层内容
变化或无法覆盖的本地序号继续拒绝 backup/verify/restore，且不发布恢复目录。

这补齐对已经存在的原归档格式的读取，不创建权限、metadata、schema、checkpoint、Node
或 event 代次，不修改原 SQL、Archive、意图、Unknown、预算、完成事实或原 owner。

## Native recovery continuation

维护的 `federation_ha_recovery_processes.py --source ... --output ...` 可在实际在线链路
通过且原 supervisor 均 native wait=0 后，只重做冷验收。它读取原配置、输入、TLS、
可执行文件 SHA 与原 final checkpoint，不构造 Node、Local EAIOS 或新原存量。新输出
使用原已声明完整 profile，经真实 recovery CLI 重新取得全部 leases、backup、verify、
restore、只读重复与缺一 Root 拒绝，再启动恢复后的真实基础设施。

原 source/result（包括 `passed=false`）保持；新结果分别记录保留输入和当前修复代码
identity。只有恢复后的实际读取与各原 leader 门禁通过才记该冷路径成功，不能从旧 JSON
状态获得控制权限。初次完整 CI 仍从正常公开入口运行整条组合。

## Validation

实际 SQL/CAS 回归验证旧 prefix、同 envelope 重复和只读字节不变；缺包、损坏、重算
外层 hash、错误或重复 Root、变更 local prefix、冲突 protected event、不同 authority
和 pending Archive 均拒绝。整个 recovery test module、strict types、真实原数据冷恢复
及最终当前 CI 共同验收；不据此推断设备停止、物理连续性或跨主机证明。
