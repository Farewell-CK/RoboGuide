# Local EAIOS 软件 worker OS quota 验收

文件计算 worker 的 admission 现在固定 `30s` CPU 和 `256 MiB` address-space 默认合同；真实
子进程在首次 permit 前应用 POSIX `RLIMIT_CPU`/`RLIMIT_AS`，父端继续检查原 kernel/time
namespace、100ms permit、取消和实际 wait。测试直接读取拥有的 `/proc/<pid>/limits`，确认
实际 soft/hard 值，而不是只检查配置字符串。

Process worker 测试 7 项通过，Memory provider 17 个 strict mypy 源文件与 Ruff 通过。该项
是 F6 的 Local EAIOS software quota slice；Control 的逻辑 quota、真实 GPU/交通成本、OS
级设备调度、硬实时和物理安全仍保持独立待办。
