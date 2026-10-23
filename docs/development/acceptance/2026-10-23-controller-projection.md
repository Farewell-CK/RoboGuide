# 2026-10-23 原 Controller SQL 投影接管

分支 `codex/nonrelease-remediation-closure`，前置 `9e8e589`；
[ADR-0164](../../decisions/0164-owned-controller-projection-installation.md)不改变 wrapper、
Node 或当前 replica v0.1–v0.3 合同。证据根目录
`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

## 原反例与修复

`supervisor-projection-baseline-01` 启动真实 mTLS Controller，通过原管理 API 接受 quota
命令，随后原进程 native wait -15。另一个实际 managed clone 持有原 SQL 的 hardlink
时，旧 materialize 仍替换主路径：原 inode `56629632` 变为 `56629644`，原 4 条命令在
新路径变为 0，alias 继续持有旧 inode。旧组合探针 SHA-256 为
`8477fa4a9742983b4caa55d76edd0e171a72a1bb688b9401b879fbbb0481ce5e`。
空 view 是明确本地测试输入，没有声明它已由 quorum 接受；这里证明原路径保护失效。

新组合函数使用 runnable Controller 的真实 sibling/inode writer 和原生旧 SQL drain，
在原 inode 上事务安装；不清理原 WAL/SHM，也不删除历史临时文件。现代 view 缺完整
recovery 在 writer 前拒绝。State 安装事务保持原命令不可变前缀，禁止回退/同 revision
分叉，复用原 `recovery.follows` 保护 copy/abort/Unknown/receipt/Catalog/manifest。
源 marker 缺失却存在历史表时不能作为空初态。legacy/v1 为原显式配置，不自动降级。

实际 Raft view 读取和 SQL 安装在同一个 owned blocking task 中；取消等待不终止其实际
SQL 作用域。安装完成仍须当前 epoch/quorum 和原 child spawn/register/process-history
门禁；复制一个 SQL 结果不创建当前执行、内容许可或设备事实。

## 实际进程与文件

`supervisor-projection-native-verified-02/result.json` 通过 15 次实际投影/导出进程调用、
3 个 Controller incarnation（native wait 均 -15）和 1 个实际 alias holder（wait 0）。
原/final inode 均 `56689548`；原 4 条命令保留，真实后续 quota 更新后为 7 条。完整
原数据重装、同一原幂等回执、legacy/v1 实际 Controller 重启均通过。

实际拒绝包括持有 alias、空 view 清除原数据、现代 metadata-only view、完整旧投影
覆盖新命令、v1 降级、删除 journal 的真实 SQL trigger 故障以及缺原 marker 的新目标。
原临时文件 sentinel 保留。SQL 故障回滚后原数据保留，重新安装同一完整当前投影通过。

最早 native 01/02 将全部物理 SQL rows 作字面比较，发现安装器首次写入
`next_event_number=0`。这是由原 checkpoint 重建、在导出 authority 合同之外的派生缓存；
源正常零事件 SQL 省略了该零值。原始失败与 before/after 全表证据均保留。正式检查只
允许该精确派生缓存重建，并完整比较原 checkpoint/raw JSON、命令、recovery 与其它
SQL values；事务、拒绝和原责任要求未放松。一次 verified 调用早于冻结探针生成而在
入口遇到 FileNotFound，未计为通过；正式 verified-02 使用独立冻结文件。

最终投影探针 SHA-256 为
`d41e00aefc10610751c4c0f92a48fc84b2c3ba0e82c6278422a9536e224b95bd`；实际 Controller 为
`3fbb973ea3e2d26074a126e9f0d37930f807780b7e80d197d1f8ae83a486326e`。其它原二进制身份
与每次命令/stdout/stderr/wait 完整保存在结果中，未用 passed 字段授予任何运行权限。

`supervisor-projection-archive-01/result.json` 将原有实际 Archive gate 接到同一个组合
安装函数。`complete-replica-state/result.json` 保留 15 条原命令、3 个实际 copy 意图、
2 个 abort 意图、原 authority ID、floor 6 和原预算；两次安装后原完整投影完全相同，
真实 Controller 能读取原 CAS bytes。原 SQL rollback、未发布 copy 清理、Root3
producer/reader、完整 cold restore 及缺内容拒绝等既有门禁仍通过。

两个实际 gates 均为 Nodes/devices/models/simulators 0；Quota 只为逻辑控制策略，没有
生成硬件、GPU、设备能力或 physical stop/continuity 证明。

## 验证与剩余范围

全库 Rust 1030 通过、2 忽略；新增实际 SQL 测试验证 valid stale recovery 不能丢弃
后续 Unknown，缺原命令的安装事务回滚。后续 supervisor 调用调整单独跑原 binary tests；
Clippy/all-targets/all-features、私有 Rustdoc、Ruff/format/函数文档和严格 mypy 原组合
356 files 按当前源检查。CI 与 Documentation 现在在同一开发分支 push 时执行原 gates，
不再依赖手动 dispatch API；没有减少任何检查，也没有新增发布/部署。

本包没有完成生产内容 profile v0.4、同一 current/joint voters bytes+metadata ACK、
Node/Mission leader routing、旧 child/原 journal 显式迁移或完整在线恢复。Snapshot reader
Drop/其余阻塞路径、N2–N7 及全部原软件矩阵继续必需，尚未宣告 SoftwareComplete。

后续自动 [CI #199](https://github.com/Farewell-CK/RoboGuide/actions/runs/39899121728)
在 Rust test 阶段失败；原 job log 已保留为 `p7/ci-199-rust.log`，72 通过/1 失败。
`controlled_leader_transfer_moves_to_caught_up_voter` 在刚确认多数派 leader 后，未观察
所选 follower 的 catch-up 就发起交接，实际生产门禁正确拒绝。测试 setup 改为先等待
这个确切目标的实际 replication acknowledgement，再发送一次原请求；未改生产门禁、
预算、断言、retry 或目标选择。单项与原已提交 replication 73 tests 通过。工作树中新加
且尚未提交的 Snapshot 阻塞反例属于下一工作包，不计入该 73 项；CI 没有 skip。
