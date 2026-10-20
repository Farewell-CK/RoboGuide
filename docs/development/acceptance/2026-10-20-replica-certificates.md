# 持久副本证书与 CA 材料迁移验收

日期：2026-10-20。实施基线 `main@3edbdf4`；对应
[ADR-0116](../../decisions/0116-replicated-member-certificate-rotation.md)、
[部署合同](../replica-certificate-rotation-v0.1.md)和
[闭合请求](../../../contracts/replication/certificate-rotation-v0.1/README.md)。

## 实际链路

实际三副本/Controller child → 启动后生成新私钥与 CA → 原 mTLS 管理 request → Raft
Stage → 原子替换目标 bundle → 每个配置成员实际独立目标 TLS → 原 Retire 多数派持久化 →
实际旧私钥请求拒绝 → 软件 leader transfer → leader 原进程继续换证 → 显式 lost-key Abort →
全部成员迁移新 CA/root 文件 → 实际旧 CA 握手拒绝 → 全体原数据库重启。

gate 创建零 Node、零设备、零模型、零模拟器，不使用业务完成 fixture 证明换证。
两个 Stage/Retire、一个 Stage/Abort 和两个其余成员的 Stage/Retire，共 10 条原不可变
操作。最终 3 个真实 endpoint 的 local trust revision/digest 相同，每份独立 CA 文件的
实际 loaded root digest 相同。普通 timer 可以提交原应用 revision；核对的完整原应用
checkpoint 字节未改变。独立真实 SQL apply/snapshot 回归证明证书命令不递增 application
revision、不修改事件或预算，并拒绝回滚到退休前快照。

## 失败、竞态与恢复

- 未换新 key：真实目标出示旧 leaf，拒绝 Retire；Stage 及原 current leaf 保留。
- 少一个实际成员：连接失败，拒绝 Retire；不改参与集合或伪造 ACK。
- 成员缺新 CA：标准 TLS UnknownIssuer，拒绝 Retire；部署 overlap 后才通过。
- 旧 key 仍有 CA 签名：实际 mTLS 可建连接，但原 sender/current leaf 权威拒绝 frame。
- leader 换证：原 PID 和 epoch 保留，并额外观察 5.17 秒；没有重启或续期执行预算。
- 原新 key 实际删除：显式 Abort 永久退休待启用 leaf；再次 Stage 该 leaf 拒绝。
- 移除 old CA：原旧私钥实际 TLS 被 UnknownCA alert 拒绝，非字符串 mock。
- 原主体后来换证：当前凭证观察 Stage/Retire/Abort 的完全相同原 receipt，不重新轮换。
- 实际旧 binary：拒绝 v0.3，完整当前 trust 未改变。
- 实际 TLS 旧 frame 先排队、后退休：拿 member writer 后重查原证书并拒绝；真实 socket/
  Raft/SQL 回归。实际签名 chain 的未来 PKI 过期也拒绝，State 时钟不变。

验收发现 supervisor 每轮重建 ctrl-c future 可能丢失停机信号；现在保留原 subscription，
在 quorum/materialization await 后再次查看已到信号，避免关停过程中生成新 child。最终
7 次受控 stop 均 exit 0；owned 子进程全回收。失败轮次 A 保留 timer-revision oracle
错误记录，后续证据分别扩充 writer 竞态、chain expiry、Abort/CA，最终以 F 为准。

## 复现与身份

```bash
uv run python tools/quality/check_replica_certificate_processes.py \
  --output /tmp/rg-cert-rotation-20261020-f --skip-build \
  --old-binary /tmp/rg-pre-v03-replica-20261020
```

重现换新 output 目录；保留原目录。`result.json`、原 request、原证书 bundle、所有独立
store、原 supervisor/child logs、原应用 checkpoint 与 status 可逐项复核。

| 项目 | 原始身份 / 实际结果 |
| --- | --- |
| Controller binary | `sha256:4fb8c66a24e06478c8fade9f913b2d17d3621a6e95d1523a2396983ed7cb310b` |
| Replica binary | `sha256:a75eb85bac36d3ed3926bf5f810a895559db079f713eb172f6786ad0c48b592b` |
| 旧 Replica binary | `sha256:1f414079fc9d6f1f853b8d4c36fd966e72592d052fd5f5a92bf35b3ab8dda15c` |
| Result file | `sha256:127508aee65352b59a07eca3b4beb8cbf4ef1b38199e4b9f00ac0c238db2724c` |
| 最终 trust | revision 10；`sha256:63dfdff410378b6392d50cb0b955f9f3fe634554b06a3515098b1c2bead6b5ae` |
| 实际新根集 | `sha256:bcb68a7e0cef789d017ced534ceffe73a465e824465da83d4fc049f522538144` |
| Owned supervisor stop | 7 次 exit 0，全部实际 processes 已回收 |

完整 Rust workspace 896 passed、2 原有 ignored、27 suites；完整 Python 2623 passed、
1 原有 skipped。workspace/all-target Clippy 与 private Rustdoc warnings denied 通过。
请求 schema 与实际原 commands 对照，Ruff/format/strict mypy/函数文档及站点 strict 检查
在交付前执行。Node Contract v0.12/config v0.13/catalog v0.15、journal v13 与 wrapper v42 不变。

## 必须继续的交付

该结果关闭 F5 的同一基础设施成员 leaf/部署 CA 迁移软件路径；固定 3–9 成员、512 命令
与 all-configured-member Retire 是显式支持边界。它不关闭旧 v0.2 数据显式升级、二进制
滚动升级、在线 archive/catalog/CAS 恢复、跨主机域故障、F0/F4/F6–F10 或整体
SoftwareComplete。设备 identity、停止、handoff、硬实时与物理连续性保持独立待验证。
