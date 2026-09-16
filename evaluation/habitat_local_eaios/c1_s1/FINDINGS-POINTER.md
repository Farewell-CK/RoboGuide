# C1-S1 evidence pointer

完整的 C1-S1 before-fix fault matrix 结论、根因定位与 CORE BLOCKER 判定见
`../c1_s0b/FINDINGS.md` 的 "Part C — C1-S1" 与 "Part D — restart truth table" 章节。

本目录归档：
- `fault_proxy.py` / `run-fault-case.sh`：注入工具（evaluation-only）
- `cases/F*`：每个 fault case 的 status observations、事件、attempt、
  node journal、bridge sqlite、四进程日志、fault proxy 日志
- 判定：`C1-S1 = BLOCKED BY CORE`（`core/node-service/src/engine/execution.rs::spawn_status_loop`）
