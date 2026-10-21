# ADR-0133：Local EAIOS 软件 worker 的 OS quota envelope

状态：Accepted；关闭文件计算 worker 的实际 OS quota 证据，不把它扩展成硬件或实时保证。

## 决定

`ProcessFileJobs` 在 admission 前固定 deployment-owned CPU seconds 和 address-space bytes，
把它们随原 worker 启动环境传给 `file_worker.py`。worker 在读取任何输入前用 POSIX
`setrlimit(RLIMIT_CPU, RLIMIT_AS)` 设置 soft/hard 同值；父进程继续保留原 kernel/time-domain
permit、deadline、取消、PID 和 wait 证据。没有 quota envelope 的直接协议 fixture 只测试线协议，
不代表 Local EAIOS production admission。

health 公开实际 quota contract 与 `posix-setrlimit` assurance；worker 被杀、超时或父权限结束
仍保留 Unknown/实际退出状态，不能由 quota receipt 变成 Task/Mission success。该 envelope
只覆盖软件进程资源，不证明 GPU memory、交通、物理执行时间、硬实时或设备 safe-state。
