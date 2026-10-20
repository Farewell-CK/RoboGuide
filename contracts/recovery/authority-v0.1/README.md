# Controller 写入禁用与恢复 v0.1

`GET /v1/authority-status` 读取进程级 first failure、generation 与完整最后提交 checkpoint
identity/authority revision。它不读待提交 SQL，不刷新任何原证据时钟。实际应用进程默认
启用；库级调用者明确安装该策略后才具有相同行为。

`POST /v1/authority/recover` 使用[封闭命令](command.schema.json)，需要实际 transport 映射
的全局 recovery-admin。字符串额外受 UTF-8 字节界限约束；空白/控制字符不能成为命令
identity。恢复核对原 process/generation/checkpoint/journal head，原 SQL 内容和 cache
必须一致；完整原 checkpoint 与恢复 command 的零事件 Commit 成功后才解除禁用。

同命令和实际稳定 principal 返回原 receipt；异参/异主体拒绝。旧 receipt 表达历史原
操作，不表示当前进程可写；它不能解除另一代故障。调用者重新读取 status 确认当前模式。
整个操作不重新执行 Mission、模型、Node 命令或 Memory import。

原故障未解除、open/pending transaction、改变的 SQL/cache/journal、未知复制 Commit 或
catalog fence 均拒绝。复制不确定性通过原 supervisor 的 accepted-state 重物化恢复；
此 API 不实现离线节点强制接管。参见 [ADR-0114](../../../docs/decisions/0114-write-disabled-controller-recovery.md)
和[操作指南](../../../docs/development/controller-write-recovery-v0.1.md)。
