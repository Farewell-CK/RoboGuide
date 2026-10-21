# Local EAIOS 软件 worker OS quota 验收

文件计算 worker 的 admission 固定独立 CPU/address-space 声明；本验收显式配置 `30s` CPU
和 `256 MiB` address-space（部署默认 CPU 与 wall deadline 均为 `5s`）。实际子进程在首次
permit 前应用 POSIX `RLIMIT_CPU`/`RLIMIT_AS`，父端核对 exact PID/限制、将声明和内核回执
保存在原 SQL，并提供只读 quota 查询。测试同时读取拥有的 `/proc/<pid>/limits`，验证旧
v1 数据迁移后声明与 proof 仍不可用；无环境配额的真实子进程在 permit 前失败。

Process worker 进程测试与实际 Unix HTTP 只读查询测试通过，Memory provider strict mypy 与
Ruff 通过。该项是 F6 的 Local EAIOS software quota slice；Control 的逻辑 quota、真实
GPU/交通成本、OS 级设备调度、硬实时和物理安全仍保持独立待办。

维护的 parent-death gate 于 2026-10-21 实际启动/杀死/恢复 backend 与三个子进程，通过
原 SQL 和 API quota receipt 一致性、旧 thread writer 拒绝、未知 reader 的 GC fence；无
Node/设备/model/simulator。`RESULT.json` SHA-256：
`c057cccc9b575043c748854fd1cfadd47f632a18bcdaaa99058e2cff26ba61f2`。
