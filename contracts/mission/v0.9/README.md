# MissionPlan v0.9

继承 v0.8 的 Actor/实体语义，Role requirements 可增加至多 8 个 `memories`。每项只包含
不可变 selector、kind、payload_schema 和 media_type，不携带 Node、consumer provider 或路径。
Control 根据当前实际注册决定接收者；缺少准备/实际导入/当前 availability 时禁止 Execute。
旧 v0.8 及以前拒绝新字段。Grounding 仍须来自已接纳来源，provider 输出不能发明 memory。

MI provider DTO 必须显式选择本合同，保留相同 semantic requirements；默认 v0.8 不自动
获得它，输出也不能通过 v0.7/v0.8 兼容而删除 prerequisites。见
[ADR-0128](../../../docs/decisions/0128-explicit-memory-plan-provider-dto.md)和
[真实进程组合验收](../../../docs/development/acceptance/2026-10-21-runtime-mi-scoped-memory.md)。
这些软件门禁不证明完整全局 Memory、typed-map 生命周期或设备事实。
