# ADR-0219: Original stop-only Federation observation

- Status: Accepted
- Date: 2026-10-26

## Context

真实远端 CI 已通过两域独立目标、在线 Archive、两域换主、分区中成员换证和 Root 再换主，
但原停止请求丢失后未在 60 秒内确认全部 stopped。原 30 秒 cleanup 同时限制了副作用与
后续 GET；分区中的 abort 未必到达，不能重发原 mutation、刷新预算或推断对端已停止。
对端仍按自己的原 probe budget 停止并释放，需要独立、有限的只读证据路径。

## Decision

显式 peer profile v0.3 在 Root 原 admission 冻结 profile revision 和
`stop_observation_budget_ms`（1,000–300,000 ms）。State 的完整 `origins` 表将每个原事务
绑定新 profile，旧事务保留 origin unavailable，不从配置升级借用权限。缺必需表/声明拒绝。
旧闭合 binary 在配置或完整 checkpoint 边界拒绝新格式，wrapper v60/Node 合同不变。

首次 stopping 创建原 30 秒 effect cleanup 与原 GET budget，使用相同实际 kernel start。
cleanup 到期后仅能进入 `observe-stop`，GET 原 transaction ID；不再发送任何 POST。
每个 GET intent 先在原 writer 中持久化，子预算不能超出原独立 scope，重启/配置改变/
backoff 不续期，最多保留十六次原观察。权限、epoch 和所有实际 transport 检查保持。

确认必须是精确原 peer 的 manifest/domain/transaction/nonce，且原 stopped 与资源释放都
可验证。它只能减少原停止 fence；原 abort/Prepare 的 None/error、nonce、全局决定与
原时钟保留。无证据或 scope 到期继续 Unknown，不把 timeout 当设备停止证明。

恢复核对全部原 admission、profile、同 start、duration、namespace 和子 GET 截止边界。
v0.2 的 30 秒实际 request budget 按原 profile 校验，不再错误套用旧 10 秒上限。

## Validation and limits

合同反例覆盖旧 profile 借用、缺 origin/声明、换时钟/namespace、扩截止时间、旧范围和
无 stopped/资源证明。真实 HA gate 将第二份独立故障输入的预算在首次 admission 前声明
为 120 秒，保留原正常 300 秒事务；分区持续到原 abort 和首个独立 GET 均真实失败。
修复网络后只读等待对端按其原期限自动停止，原 abort Unknown 保持，再做完整双域恢复。
不以新 ID 重发原控制命令，不运行设备、外部模型、模拟器或 E6。
