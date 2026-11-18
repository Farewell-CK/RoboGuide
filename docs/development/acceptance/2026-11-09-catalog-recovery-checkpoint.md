# 2026-11-09 Catalog 恢复快照：软件验收

本轮实现 [ADR-0243](../../decisions/0243-catalog-recovery-checkpoint.md) 的显式 standalone
Controller storage v2。默认 legacy 行为保持；没有在现场启用该格式，没有连接机器狗或
提交新物理任务。性能实验全部在本地 WSL 中运行，单个恢复进程限制 2 GiB 地址空间与
CPU 时间，使用独立 SQLite 副本和 loopback 监听。

## 已实现

- 第一次迁移在实际 writer lease 和独占 SQL 事务下检查原 checkpoint／事务账本及完整
  连续事件尾部，生成带覆盖序号、全局时间上界和有序摘要的目录恢复数据。
- 每次 append 在原事务内同时更新原事件、恢复投影和序号。失败／中断／显式 rollback
  不会把源日志与恢复覆盖拆开。
- 正常启动只核验目录相关记录、原始 source envelope、覆盖与摘要，不扫描旧遥测正文。
  旧 telemetry 的全面损坏检查仍通过完整历史审计完成，不能宣称快启动同时审计了全部历史。
- 迁移不删除原事件，不改写 Task、Commit/Bind、取消、Unknown 或身份授权。
- 明确 user_version=2 与闭合 marker；实际旧程序拒绝打开。缺失投影不自动重建，
  不通过移除 marker 降级。现有 v1/legacy 继续受支持。
- 新格式暂不接入 replica 导入／导出与通用 recovery-tool，相关路径拒绝；完整离线
  SQLite 备份保留其所有表。受保护目录记录上限 16,384 条／64 MiB，超限拒绝事务。

## 原始数据完整性

使用此前升级保留的 1,951,744,000 字节数据库，未截断历史、未删除 telemetry：

| 内容 | 条数 | 迁移前后逐行摘要 |
| --- | ---: | --- |
| events | 801,957 | 相等 |
| controller_checkpoint | 1 | 相等 |
| control_command_journal | 419,021 | 相等 |

恢复所需的目录记录为 25 条，payload 合计 13,920 字节。原全历史 payload 为
1,440,132,599 字节。完整 checkpoint 的 SHA256、事件末尾序号和时间上界迁移前后均相等。

## 验证范围

State 单元覆盖：旧数据迁移、已归档前缀、后续 append、未提交/rollback、实际 SQL 写入失败、
原始 checkpoint/journal 损坏、尾部缺行、快照缺失/多行/错误摘要、源事件被替换和旧 writer 拒绝。
新模式仍完整保留任务和目录来源校验；普通历史审计与启动校验范围有明确区别。

真实私有 Controller/Node 的 `posture_goal_process_gate.py --storage-schema 2` 验证普通
任务、目标等待／拒绝／满足、原回执去重、依赖只执行一次、Node/Controller 冷重启和资源释放。
使用 synthetic provider，不验证真实姿态或任何硬件动作。

维护的存储进程检查：

```bash
python tools/quality/check_catalog_checkpoint_processes.py \
  --probe /absolute/catalog_recovery_probe \
  --snapshot /absolute/retained-copy.sqlite3 \
  --previous-controller /absolute/previous-integration-server \
  --output /new/private/evidence-directory
```

该脚本只复制输入，按 streaming→首次迁移→v2 重启依次启动独立限额探针，比较原始表摘要，
并检查真实旧 Controller 拒绝新数据库且原文件字节不变。每次运行要求新目录。

## 性能解读

最终同机完整 Controller 对比：

| 模式 | readiness 时间 | 采样到的峰值 RSS |
| --- | ---: | ---: |
| 第一阶段流式恢复 / legacy | 4.020 秒 | 48.16 MiB |
| v2 首次迁移并启动 | 5.358 秒 | 42.50 MiB |
| v2 后续重启 | 0.068 秒 | 41.98 MiB |

目录恢复探针的独立结果：流式 3.716 秒；首次迁移 5.323 秒；v2 重启 0.001913 秒。
后者只衡量打开存储及恢复 Catalog，不能当作整个 Controller 就绪时间。
规模复测（1 万/10 万/100 万 synthetic 心跳）每次只读 1 条受保护记录，重启探针分别
约 0.000864 / 0.000825 / 0.000756 秒，峰值 RSS 约 5.36 / 5.57 / 5.68 MiB。

140 项 State、318 项 Controller 测试和 17 项真实软件进程检查通过。
workspace all-targets/all-features Clippy（warnings denied）、rustfmt、相关 Python Ruff、
strict mypy 与函数文档检查通过。最终 Rust 构建使用独立 target，排除了其他工作区缓存。

完整 Controller release SHA256：
`264808120b32c229083d496123026f139d76fd41858e11b0515386874035b96f`。
恢复探针 SHA256：
`884caa298036e195bcd74ff600df10438a6b3c78ad4e83078f9b5d447c01355f`。
实际旧 writer SHA256：
`8851943698fddc33fac870809d9d9777e01ca527bb12cafd464a48867aecce46`；
它以旧存储 profile 启动时明确拒绝，v2 数据库文件摘要未变。

测试使用相同 WSL 主机、OS 文件缓存未清空、无在线 Node，整体进程还包含 TLS/API
初始化和终态历史恢复。因此这里报告单次原始测量，不当作多次分位数或生产 SLO。
不能将该结果与此前 ThinkPad 的约 90 秒／5.6 GiB 直接计算倍率：机器、负载与版本不同。
也不能把冷进程启动说成清空操作系统页缓存后的冷盘启动。

全进程测试仅对数据库副本增加一份离线测试管理员授权，保留原授权记录及 Node registry；
使用本地生成的测试 CA，未获取真实机器人私钥。检查原 48 个 Mission、48 条 attempt、
原 Node/Artifact 授权、无 Unknown、零占用和无真实 Node 连接。存储层原始表摘要检查另用
完全未改授权的副本。

规模测试使用明确 synthetic 心跳，固定一条目录记录，比较 1 万、10 万与 100 万历史；
恢复读取条数始终为 1。它验证与无关遥测规模解耦，不能证明无限目录规模也为常数成本。

## 边界与后续

此版本解决启动读取路径，不减少磁盘已有历史或后续高频写入。遥测去重、有限热窗口与
受控归档仍是第三阶段；Task/Unknown 证据和幂等账本不能按普通日志直接清理。
现场部署和新存储迁移仍未执行，需保留同版数据回滚基线后进行独立现场验收。

所有失败输出保留：首次全进程夹具错误使用静态 operator profile，被原权限恢复校验拒绝；
随后改为在私有副本中提交单调增量授权。共享 Cargo target 混入另一开发线产物的失败，
由改用独立 target 后完整重测解决；未改其他工作区或放宽生产恢复检查。新增原权威校验后，
旧测试夹具缺 checkpoint 导致拒绝，已补齐该夹具的原事务 checkpoint。

证据目录：`C:/workspace/DEAIOS/lab/rg-catalog-checkpoint-20261109/`。
完整私有 SQL/测试证书仅位于 WSL `~/rg-lab/catalog-checkpoint-20261109/`，不加入 Git。
最终日志使用 isolated-*、field-check-reviewed.log、full-controller-reviewed.log、
controller-v2-reviewed.log、scale-reviewed-final.log，初步结果和原失败日志均保留。
