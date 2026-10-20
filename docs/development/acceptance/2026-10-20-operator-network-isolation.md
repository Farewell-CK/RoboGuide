# Operator 网络预算与已提交读取验收

日期：2026-10-20。设计：[ADR-0119](../../decisions/0119-bounded-operator-network-isolation.md)。
当前工作完成该软件入口切片；原 F0/F4–F10 的其余必需项继续实施，不声称总体完成。

## 问题与结果

原 Operator 的同步授权/查询/inline mutation 会占用单个 Tokio worker；相同已提交授权
也等待 writer。另有共享 registry、identity/attestation、verifier、Memory 入口和 Mission
admission/resume/review 把 live Controller mutex 持有至慢 SQL 或 CAS 工作结束。

当前请求 poll 复用原同步 worker handoff，TLS 前有界 permit 覆盖完整连接，response
write/shutdown 有原 30 秒网络预算。相同完整授权的读检查无需排 writer，改变的授权和
所有 mutation 仍使用真实原 TLS witness/当前委托和原 writer。私有 candidate 与 SQL
期间释放 live mutex；Commit 后才发布。metrics SQL 查询在短锁只读快照之外。

实际单 worker/四个 TLS permits 的检查：第五条连接在请求前拒绝，独立 Artifact health
仍回复；释放后观察 active=0、limit=4、rejected=1。真实 SQLite writer 被外部锁住，
并发 metrics 请求已读取原授权文件时，health 以 200 在约 0.0096 秒回复。
原 inotify 排队撤销中，第一条 registry revision 1 提交，第二条返回 409 且没有提交。
重启保留稳定主体和原幂等 receipt，授权降级继续拒绝，实际 registered Node facts=0。
这个短延迟只描述本次观测，不作为 SLA。

## 反例与保留失败

在独立 detached `596389f` 上只加入同一实际 mutex/TCP 回归，生产代码未变：
`/tmp/rg-operator-worker-old-negative.log` 因原 mutex 五秒超时失败，当前实现同项通过。
旧日志 SHA-256 为 `b0f378bfd69e57f96ea5cb86d34e4d58ca218cea7665e9d3dce3900af6a4e75a`。

实际旧 Controller `de4c94e64c54d7bf84198e008d85022431a0fcc04ab4f0db94d25547e92f6440`
用同一身份进程检查、1 worker 执行；在 SQL 排队期间无法观察另一个 metrics 的原授权
文件读取，保留 `/tmp/rg-operator-identity-old-negative-20261020.log` 的超时。
未改旧 binary 或把观察缺失记成成功。

早期 `/tmp/rg-operator-identity-20261020-a` 容量门禁通过，但 SQL 阻塞期间 health 超时，
促使进一步查出共享 registry 等函数持有 live mutex。修复后 B 通过；A 原日志保留。
不把这些已定位路径泛化为所有历史 HTTP 中断的解释。

## 实际软件进程证据

本轮实际 Controller SHA-256：
`9b8bf8bca2a8b56a88e9ba4d60c3cbe2841c8dcea93e4c73278fb6997a1d7b2a`。
实际 Replica SHA-256：
`32c251c061f67a8d0d93960513b9aed87debe90069df9e9ec88660d17003544c`。
Node journal v13 binary 保持
`b414b3cabab99982938449e5ca170debb32906d950f60b15ffa765c6865f9147`。

| 目录 | 实际检查 | result.json SHA-256 |
| --- | --- | --- |
| `/tmp/rg-operator-identity-20261020-b` | mTLS 容量、阻塞 SQL/metrics/health、撤销、主体幂等、重启；零 Node facts | `5a79a280d7540de36d0b30b206e73fb3a4aa4579d56b5e279d4a2691536c57e6` |
| `/tmp/rg-operator-file-recovery-20261020-a` | 1 worker、两个真实软件 Node、两个实际 CPU hash jobs、独立 verifier、原 grant/revoke/reclaim/重启、两次 SQL 故障恢复 | `d915520d1e45fdeec3fd5643707581cbde7f395fce5bc7ee4f76baf6d58bf115` |
| `/tmp/rg-operator-certificates-20261020-a` | 1 worker、三实际 Replica 的 leaf/CA 迁移、lost-key Abort、重启；七次 supervisor 正常退出 | `923adf56db204ea8c01a6ffbc88af8a722d3b16c2cb2dee23a082eb3bffbd0a6` |
| `/tmp/rg-operator-cold-recovery-20261020-a` | 同一 binary、六 SQL/39 tables/原十条换证 receipt、真实 CAS 新目录恢复、三个实际 leader HTTP 读取；六次正常退出 | `799fd25c7fa1f5b8de81b4edacc00e08dd5486c7d2ae4142cf5721030f5d5916` |

文件链运行实际 Prometheus/Alertmanager，专用无 mutation 权限的 mTLS scraper，以及
独立 owned loopback receiver；两次 SQL 故障各有 firing/resolved。新容量 warning 规则
随本轮共十二项规则经过原生 promtool 验证；不声称容量 warning 已有独立投递验收。
所有 owned 进程已回收。devices/models/simulators 均为 0；文件链的两个软件 Node 来自
真实配置和注册，不生成设备、硬件身份、物理能力或停止事实。

## 质量与边界

Rust workspace 27 suites：904 passed、2 个原有 ignored；原 ignored 未作为通过数。
单 worker HTTP 与 response backpressure、连接部署范围/原 policy digest 有回归。
Python 全量 2626 passed、1 个原有 skipped；未用软件 fixture 替代设备验收。
workspace Clippy all-targets、private Rustdoc warnings-denied、Ruff/format、primary strict
mypy 231 files、observability strict mypy、函数文档检查、rustfmt、diff-check 和 MkDocs
strict 通过。Clippy 初次指出测试模块后的 item 排序，已将 cfg(test) 模块移至文件尾；
未修改生产行为。文档构建保留原有 absolute-link/anchor INFO。

本项不实现其他 listener 的完整连接容量、长期/跨主机 SLO、完整升级/在线内容恢复、
全局 CAS 删除、运行中抢占/计划修订或跨域协议，仍按原计划继续。当前 wrapper v42、
Node Contract v0.12/config v0.13/catalog v0.15/journal v13 和原预算不因本项改变。
