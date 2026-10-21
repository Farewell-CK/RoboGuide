# Relation violation whole-Group stop 验收

Runtime relation violation/Unknown 事件现在进入应用安全停止 authority，并为同一 Group 的
全部当前非终态 attempts 建立独立 durable stop purpose。原有单 attempt stop、deadline、
safe-state、worker exit 和 release gate 保持不变；Group 中任意未确认 attempt 都继续持有
ownership。

Rust/应用编译回归覆盖现有 integration-server safety-stop 与 relation tests；该项只接通
软件停止协调，不证明设备物理停止、hard-realtime hazard 或异构 regrasp。
