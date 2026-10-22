# 原子进程 Broken pipe 与真实 wait 回执

本修复在唯一分支 `codex/nonrelease-remediation-closure` 接续 `702b799`。
它修复 ADR-0124/0139 原实际 worker 的回执保存顺序，不引入新 authority、schema、
Node、设备或超时预算。

## 反例与修复

远程 `702b799` 的实际 MI gate 失败，原日志和 Controller SQLite 来自
[失败 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39801965830)。
原档案为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/recipient-ci-native-failure/`。
SQL 显示自动修订已接纳，phase 为 Stopping；原 TaskFailed 原因为 `[Errno 32]
Broken pipe`，Memory revoke 已有回执，但持续 safe-state observations 为空。
Controller 在缺真实 stop evidence 时正确保留 ownership，没有超时后自行放行。

进程路径发现实际 `process.wait()` 已获取原 returncode，但 `stdin.close()` 可能
再次 flush 缓冲并抛出 EPIPE，跳过随后 `_record_exit`。新增反例只创建实际 owned
Python 子进程，在首个控制写入前 terminate/wait；实际 flush/close 抛 Broken pipe。
旧代码在读取 observations 时为零，反例失败日志 `broken-pipe-counterexample.log`
原样保留。此测试不是虚构 PID/exit code，也没有创建 Node 或设备。

修复先持久同一实际 wait 结果，再在 finally 关闭两条管道；仅忽略关闭已退出子进程
管道时的 BrokenPipeError。SQL 持久化失败仍向上失败，不补造 exit proof。原工作
保持 Failed、无 Cancel receipt、未读取的 quota proof 仍 unavailable；重启与同
invocation 重试不再启动原进程，不把失败改写成成功。真正拿不到 wait 仍保持 Unknown。

## 验证与范围

`test_child_exit_before_first_write_retains_wait_after_actual_broken_pipe` 验证原 PID/
returncode、safe-state、独立无 Cancel/无 quota proof、GC reader fence、原重启与
exact replay。实际 MI gate 使用原 v50 binary 与当前 provider 修复，另保留原
source/binary/config identity；外部 provider/model、设备和模拟器为零。

`pipe-mi-native` 的实际原 Request/自动触发/独立 Review/Memory/两个新 Task/
独立原目标链已通过。原 Controller binary 为
`1c36eb8b167e5439593cb8152cfbc9a5299e725e8b2c0443bca367ff3eeef49a`。
provider 原进程专项与全模块测试、Ruff/19 文件格式、19 源文件 strict mypy 和
函数文档通过；这份证据未改变原停止 freshness/deadline。

完整计划其余工作和设备停止均继续独立验收。不能由这项 OS wait 修复推断在线多数派、
物理连续性或 SoftwareComplete。
