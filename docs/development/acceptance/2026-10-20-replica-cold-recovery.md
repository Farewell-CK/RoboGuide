# 完整副本静止恢复与实际 CAS 接续

日期：2026-10-20；基线 `main@6e3c89f`。对应
[ADR-0117](../../decisions/0117-quiescent-replica-cluster-recovery.md)、
[Recovery v0.2](../../../contracts/recovery/bundle-v0.2/README.md)和
[工具](../../../apps/recovery-tool/README.md)。

## 实际结果

从已完成同一成员证书/CA 迁移的原三副本数据，经独立 recovery CLI 备份/校验/新目录
恢复，保留原 10 条操作和原身份，再启动实际 supervisor/Controller。原源数据没有被拿来
重新执行测试工作：第一份完整备份生成工作副本，后续实际上传和恢复只在新目录执行。

工作副本通过真实 leader transfer 让三个成员各自启动原 Controller/Artifact writer。
实际上传当前 `core/domain/src/memory.rs` 的 27,120 字节，正式 HTTP 接受原 upload、
完成 CAS 哈希/fsync 与持久 receipt。停全部实际进程后，v0.2 获得原 supervisor/Raft/
Controller/Artifact writers；活跃 supervisor 原锁独立造成 backup 拒绝，无 partial。

完整 bundle 包含 6 个 SQL 数据库、39 张表、3 份 CAS，以及明确的配置和软件测试凭证。
每个原 SQL schema/row 由独立 sqlite oracle 核对，不只比较 checkpoint。原 source
完整 finalized CAS 内容仅存在一个基础设施副本；显式同集群集合恢复时向另两个新
Artifact 目录补齐已核验内容，独立 source/target/digest/size 保留在 v0.2 receipt。
staging 不传播，SQL、原 ACL、source、admitted/deadline/prepared/completed 时间不重写。

再启动实际恢复集群，逐个真实 leader transfer，三个正式 endpoint 分别读出相同内容，
独立 SHA-256 相同；相同原 finalize 请求返回完全相同的原 receipt。Raft 的原投票、
membership、完整 log、snapshot 和 applied lag 留给原共识机制处理，没有编辑 SQL、
清零 term 或人为选择成功副本。6 次受控 supervisor stop 全 exit 0，全部 owned process
回收。Node=0、devices=0、models=0、simulators=0；这是内容/基础设施证明。

## 复现与证据

```bash
uv run python tools/quality/check_replica_recovery_processes.py \
  --from /tmp/rg-cert-rotation-20261020-f \
  --output /tmp/rg-replica-cold-recovery-20261020-c
```

重现使用新输出路径。`complete-profile.json`、原 SQL oracle、完整 bundle、restore receipt、
三个原/新 supervisor/child logs 和真实 HTTP 结果留在该目录。实际旧工具拒绝 v0.2 的
独立证据是 `old-tool-refusal.json`；完整脚本也支持 `--old-recovery-root` 执行同一门禁。

| 证据 | 原始身份 |
| --- | --- |
| 完整 manifest | `sha256:79fcca7ce02e2f459bca3d9c64605d024827e078cba87a662b99c17354b4d9e1` |
| 实际内容 | `sha256:356918ef34b7e521f7278fb85636b2d77a32297a31d0393affee1cb4fcae573a` |
| Result file | `sha256:8f5a1d399746ef4c51f787d1971f4ee2cdf234512ec4884483b2c616d9bc9ffe` |
| 原 SQL oracle | `sha256:78829a8dd0d3014ddd71da5cbe13ebab6f26e10be40a0b8bf7174942be5e06b0` |
| 旧工具拒绝证据 | `sha256:acd44e07c65c568807fc9e3cef60365612c00ebdbd5596c1285321bf70df7f1b` |
| Controller | `sha256:4fb8c66a24e06478c8fade9f913b2d17d3621a6e95d1523a2396983ed7cb310b` |
| Replica | `sha256:a75eb85bac36d3ed3926bf5f810a895559db079f713eb172f6786ad0c48b592b` |

## 验证与未关闭项

Recovery 18 个测试通过，包括实际 supervisor lock、旧 profile 拒绝、完整 SQL/CAS、
跨 cluster/缺成员拒绝、未完成 staging 不复制，以及缺失已完成副本的只读重试拒绝。
完整 Python 2626 passed、1 原有 skipped；Ruff/format、strict mypy 和函数文档通过。
Rust 未修改，沿用同一二进制的 896 passed/2 原有 ignored、Clippy/Rustdoc 检查。

原 A 的上传 finalize 超出 5 秒客户端窗口；原 B 在 30 秒窗口发生已持久 Started 后 HTTP
断开。二者保留失败证据，没有更换原 ID 假装重试成功；C 的原实际完整调用链通过。
这些间歇 HTTP/调度中断仍属于 F10 必须定位的可靠性问题，不能据 C 推断延迟 SLO 或
稳定性已达标。完整脚本在失败时只对自己拥有的 child 尝试有界、无参数值的栈诊断，
不查找或停止外部服务。

本项是声明同一集群的静止、同版本完整恢复与 CAS 接续。在线跨主机内容复制、自动
snapshot/archive 全周期、旧 profile 升级、滚动二进制升级及 F0/F4/F6–F10 继续必需；
不声明整体 SoftwareComplete、设备停止、硬件身份或物理连续性。
