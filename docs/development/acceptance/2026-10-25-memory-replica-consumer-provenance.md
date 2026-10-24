# Memory replica consumer provenance acceptance

状态：实现完成，跨模块运行矩阵继续纳入 N1（2026-10-25）。

本切片把新 generic 与 typed-map replica evidence 绑定到原始 Memory import 的
`TaskRef` 和 `execution_id`。Node 从 canonical import invocation 生成 provenance；Artifact
HTTP 在写事件前把它与 Controller 的原始 `MemoryImportIntent` 对账；缺失、未知 import、
Task 不同或 attempt 不同都会拒绝。State/Catalog replay 保留该字段，旧事件解码为
`None`，不从 Node/provider 推断。

event payload codec v33 只用于带 provenance 的副本事件；把这类事件降级标记为 v32 会在
replay 前拒绝。实际 integration-server typed replica HTTP/replay 测试、State codec fence
测试、Node Service 136 项回归和受影响 Clippy 已通过。该切片只提升引用审计能力，不证明
全局内容可用、设备停止或物理连续性；N1 的 Archive/Snapshot/GC 全链仍继续。
