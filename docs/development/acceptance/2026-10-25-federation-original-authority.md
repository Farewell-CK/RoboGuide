# Federation 原始权威与回执验收

ADR-0202 修复 ADR-0201 首包的确定问题：Prepare 可直接携带 Committed；相同命令在其他
checkpoint 变化后重写 journal 导致 identity conflict；domain grant 没有版本水印；本域
Committed 被误当成全局 readiness。当前服务使用 v0.2 和 wrapper v54，保留原阶段回执、
完整原输入、actual TLS/stable principal、tenant/domain pair、原策略与同 kernel budget。
相同原主体重试只读回原回执，不续期、不重写 journal；实际 SQL Commit 前再次检查原
transport、策略与预算。原 v53 无归属 session 保留历史，不能补造新的 service grant。

本次证据根为 `/data/workspace/roboguide-audit/federation-boundary-v54-20261025/`。
`run-traced/result.json`、逐域 `http-trace.jsonl`、独立 `participant-*-sql.json` 与
`source-identity.json` 保留原始过程。实际 Controller binary SHA-256 为
`a03723b676d1016e80d09a93d23a8cc79d94a7538569cbcc1ca6a3367e664a72`；原 v53 binary
SHA-256 为 `b19ca3270a3e67851ca653929c6c227864bf5c56327f631ce26aad0692b4d0d3`。
证据不包含任何设备、模型或模拟器执行。

| 原路径 / 反例 | 实际观察 |
| --- | --- |
| TLS 请求完整发送后关闭连接 | 独立 SQLite 观察到原 Prepared；重试复用同一回执和 journal 行 |
| 两独立 Controller / SQL / CAS，停掉其中一个进程 | 存活方只能报告本域 Commit，global dispatch 为 false；另一域重启后仍为原 Prepared |
| 相同 Prepare 在 Commit/Abort/过期或其他命令之后重试 | 返回首次 Prepared 回执；原 budget、principal、policy 和 journal 不改写 |
| stale epoch、digest、其他原主体、wrong tenant/domain、直接 Committed、过长预算 | 原有 handler 拒绝，未创建对应新权威 |
| native SQLite trigger 拒绝原 journal INSERT | checkpoint/Control/receipt 整体回滚，进入 write-disabled；显式恢复探针后原命令可重试 |
| 最终 transport witness 在写 SQL 后拒绝 | 原 checkpoint、journal 和 live candidate 均保持不变 |
| 域策略 revocation、rollback、进程 restart | 撤销持久化且重启保留；回退拒绝；轮换 leaf 只可查看同一主体的原回执，不能借新策略补新 Commit |
| 零事件 / 零 session、仅有已登记域策略的 v54 | 实际旧 v53 binary 启动拒绝，逐表权威行不变 |

旧 binary 打开 SQL 时改变了主文件/WAL 的落盘形式，不能报告“文件字节没变”；WAL-aware
完整 SQL 行比较证实原 authority 未变化。当前旧 writer 门禁分别记录这两个观察。

复现命令：

```text
uv run --frozen python tools/quality/check_federation_service_processes.py --output <new-dir> --old-controller-binary <actual-v53-binary>
uv run --frozen python tools/quality/check_federation_schemas.py --evidence <new-dir>
cargo test --locked --workspace -q
```

本次 workspace 通过，保留 2 项原有忽略；integration-server 为 281 通过，State 为 127
通过。恢复工具 193 项通过，维护的 CI mypy 范围 385 个文件通过，相关 Clippy/Rustdoc/
Python 格式、lint 和函数文档检查通过。离线 schema gate 核对了实际保留的 21 次 accepted
请求、3 份策略和 6 个 original records，未重新发送任何命令。

另复核原 `e30338be` CI 的真实失败日志：Archive checker 分别 bind/close 申请端口，产生
`Address already in use`。现用实际同时持有的整组 sockets 选择不同端口，复跑
`check_controller_storage_processes.py` 的 13 个 owned processes / 完整原 Archive 冷恢复
通过，证据在 `storage-archive-recheck/`。此修复不放宽原恢复门禁。

N6 仍未关闭：当前 checker 是两个 participant 的外部调用者；持久 coordinator、真实
Controller-to-Controller 请求、各域资源/epoch 证明、跨主机时钟转换和完整恢复组合仍须实现。
部署 epoch 是显式预期，不是远端 Raft observation。`global_dispatch_ready=false`；没有
Node 注册或 Execute，不证明设备停止、物理连续性、完整 HA 或 SoftwareComplete。
