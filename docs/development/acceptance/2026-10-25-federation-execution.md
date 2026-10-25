# 跨域资源协调本地软件验收（2026-10-25）

[ADR-0211](../../decisions/0211-durable-cross-domain-coordination.md)与[合同](../federation-coordination-v0.1.md)的双域软件组合通过。
实际门禁 `/tmp/rgf60-6/result.json` 的三个案例均通过，所有冻结输入在执行期间未变化。
每个案例使用两个独立 Controller、各自 SQL/Artifact、实际 mTLS 和两个真实软件 Node；设备、外部模型、模拟器均为 0。
单机进程证据不提供跨主机、设备停止、物理协调、长期 SLO 或整体 SoftwareComplete 证明。

| 案例 | 实际结果 |
| --- | --- |
| 正常双域 | 正常 Control 先持有两域资源；故意扣住一个实际 Commit 回复时两域 CPU 均未启动。全体原 Commit 回执齐备后才 release；两个真实 CPU 子计算及独立本地/全局原目标通过 |
| Prepare 响应丢失 | Relay 在实际参与域 SQL 提交后丢弃回复；原 Unknown 保留，通过真实 GET 取回原 Prepared。原 Prepare POST 总数为 1，没有重新分配资源或重做 CPU 工作 |
| 准备中原实例被终止 | 实际远端 Prepare 已提交但回复未回时，原 Root 被 SIGKILL，实际 wait=-9。重启保持原预算/nonce；原 writer epoch 失效使事务停止，两域资源在执行前撤销，CPU 子工作数为 0 |
| 已完成记录重启 | 两个成功案例重启各自原 Controller，原协调/目标/决定历史不变，CPU invocation 数不增加 |
| 冷恢复 | 三案例各自两份完整声明 Controller/Node/CAS/CPU 文件 backup/verify/new-root restore/retry，原 SQL 与文件逐项相同；恢复启动服务数为 0；genuine v59 程序实际拒绝 v60 |

Controller SHA-256：`8fe5d9ded6ae2c3e4ffce89fb7bcd9a69d4c3fb56aa0929e885e408deb12bb9d`。
Node SHA-256：`fe2e7c2efb124076af66e17c0e72b2f53151cf0e94501da110f0128477715edc`。
genuine v59 Controller SHA-256：`caf8d4d7d748c3154671bc6a5b0d2ed14f4a6920cd5393cc7cbc7b4783bd70e4`。

| 案例 / owner | 实际固定 manifest |
| --- | --- |
| normal / 0 | `sha256:9715cb7eb233cff355c60dfa2bd6d87321b00634daf15cf7025b4ce01aa1be50` |
| normal / 1 | `sha256:a2e0f8281bd015f482671c301bcec7e7115c1381542f5d61c3986011c7f3101e` |
| prepared-restart / 0 | `sha256:ea556f17b86b92a2c9ae40bbac95b1e5b49c4bcb4b97222fe3bc85a596d52f0f` |
| prepared-restart / 1 | `sha256:c282e997414b484f3ff4826bb2e0e406a99a94dac1efc474bc2b6bfede1fef0e` |
| reply-loss / 0 | `sha256:75ecbb041ac0e767d62d48ad2df29c9a8b6d4539e5fad71a6b9033bbe5e98138` |
| reply-loss / 1 | `sha256:3969b8a0685fc62a453b7f9d4b554b31cdf120d69881c83155e06ac24e1dc206` |

当前整批 Rust 1199 项通过、2 项忽略；Clippy 与私有文档 warnings denied 通过。
恢复工具 280 项、严格类型检查 402 个源文件通过；共享 Rust/Python 浮点身份 fixture 与 12 代 JSON 排序历史分别核对。
原 v0.2 metadata participant 的语义和 digest 不变。新 v0.1 身份用显式 JSON 类型与 IEEE-754 bits，保留负零并避免 literal-tag 别名。

早期实际失败分别暴露 tenant selector、独立原目标接线、原 writer epoch 和启动 goal coverage；原失败目录保留。
最终 SIGKILL 故障注入保留一次 relay BrokenPipe，表示原连接已关闭；没有将它当成未提交或未执行证明。
TLS 凭证仍是显式部署输入，声明数据恢复不代替证书/Secrets 重新部署。
本模块证明完整三案例及声明数据恢复；原证书/分区/HA/全局内容组合仍需在固定最终 profile 中完成。
未合并 main、未部署或发布；所有开发保持同一分支。

附随兼容验收：固定 `/tmp/rg-federation-compat-v60-controller` SHA-256
`98bb684c7ec95156d19270c227240b9e277e215df67ebeb3890e134a0f8aa29d`，
实际旧 participant v0.2 的响应丢失、SQL 回滚/恢复、分区、原预算过期、策略回退、撤销/重启均通过
`/tmp/rgf60-participant-2`；Artifact SQL 的十个真实故障案例通过 `/tmp/rgf60-artifact-sql-2`。
前一 Artifact 批次完成十例后因并发构建替换二进制而拒绝整批；该失败保留，不计通过。
单 package 与 Controller/Node 联合构建的实际二进制身份分别记录，不把不同 SHA 当成同一文件。
双语站点严格构建通过：851 HTML、46 条维护路由、0 个 unresolved archive anchors、9 项站点测试。
