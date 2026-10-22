# Artifact 原 Snapshot 生产者验收

本包在唯一分支 `codex/nonrelease-remediation-closure` 接续 `1033352`。
其[完整 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39746695515)成功，包含
quota 和新增 Catalog/retirement/handoff/MI/cold-recovery 实际门禁。main 保留原基线；
本包不关闭完整 N1 或其余 N2–N7。

[ADR-0151](../../decisions/0151-original-artifact-maintenance-reference-journal.md)新增显式
Artifact storage v2，在原 root writer 内保留 v1 原 backend/inode/bytes，先持久
原升级 intent，建立 mandatory journal/header，再原子替换原 owner/fence。重启沿用
原升级记录；accepted v2 缺 journal/header/owner 即拒绝，不重建。默认 legacy/v1
保持原语义，实际旧 binary 拒绝 v2。

显式 [recovery v0.11](../../../contracts/recovery/bundle-v0.11/README.md)在全部原
writer/inode leases 内由实际复制者生成 immutable Snapshot intent，绑定实际 owner、
完整内容集合、command/profile/destination、generation、UID/boot/time namespace
及不续期 deadline。intent/pins 先于读取和复制；相同原 command 中断后不能再复制。
完整 bundle v0.3 发布并独立验证后才发布原 release receipt。readonly reconcile 不
复制；明确 abort 须所有原 writer，封住未发布同步 copier 并保留 partial，不是
分布式事务或设备停止证明。恢复保留拍摄时 active intent，不写入源后来的 release。
原 Memory SQL v4、Replica storage v3、Catalog/lineage 与预算保留。

## 实际进程证据

证据根目录为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`，实际运行通过：

```bash
uv run python tools/quality/check_artifact_snapshot_processes.py \
  --skip-build --output /data/workspace/roboguide-evidence/nonrelease-closure/p1/artifact-snapshot-native-final
uv run python tools/quality/check_artifact_owner_processes.py \
  --skip-build --reference-journal \
  --output /data/workspace/roboguide-evidence/nonrelease-closure/p1/artifact-journal-owner-native-final \
  --old-controller /data/workspace/roboguide-evidence/nonrelease-closure/p1/catalog-validated-integration-server \
  --old-recovery-ref 10333524200201e7fe0b60eb86a0c8798424d070
```

第一个门禁由生产 layout probe 创建实际 owner 和 8 份各 16 MiB 的 opaque file。
实际原 CLI 持久 intent 后，native waitid 确认 SIGSTOP；另一个 CLI 因原 writer
活跃拒绝复制。原进程 SIGKILL 后 actual wait=-9，原命令重启拒绝重复制，原 deadline/
content set 不变。明确 abort/retry 保留原 intent/partial，不复活原命令。

另一个实际 CLI acquire 后被暂停，对原 operation 目录 chmod 禁止 receipt 写入后
恢复：bundle 已实际发布，回执写入失败，原引用仍 active。恢复写入权限后原 command
只验证已有 bundle 并补齐精确回执；bundle 每个原文件 inode/mtime 和原 intent 不变。
冷恢复实际 owner 不变、原 active intent 留存，经 readonly reconcile 退休；独立读
8 份内容共 128 MiB，与原 manifest hash/size 一致。

第二个门禁经实际 mTLS Controller：v1→v2 保留原 inode/owner/bytes，实际上传一次，
冷恢复后独立读两份 bytes，Viewer 伪造 admin 拒绝，缺 owner 不重建。真实旧 Controller
使用其支持的 v1 配置后因新 owner schema 退出 1；其 SHA-256 为
`7bf3ddfc2d608e8a1814bd526961b6bebb733a248b2f8c545fd40e35d1478cc1`。
真实旧 recovery 从上述 exact commit 提取，使用它支持的 v0.10 profile 读取 actual
v2，因原 owner schema 在 publication 前拒绝，不以新工具选择旧 profile 替代。

当前 Controller SHA-256 为
`d5f4d1b1e694968e78d0be2a3f7b7ee8ba05a82243035bb1b6d9865880e10000`，最终
layout probe 为 `02af7fd6c7093ec9c9fe308410f0386bf3f889eb7be72d8682c803632512ffc5`。
所有 owned process 实际回收，0 Node/device/model/simulator。

`artifact-snapshot-native-01/` 已走到最后的独立校验，但运行时维护源码变化导致最终
identity gate 拒绝，没有通过结果。原失败保留；冻结源码后在新目录复跑到 final。

合同回归覆盖原身份/活跃 writer/缺 mandatory journal/持久升级窗口、复制前 pin、
中断后 budget 不续期、精确 release/abort/retry、冷恢复 active、原 owner/intent
改变拒绝、repin 后缺内容/descriptor。Artifact 24 项、recovery 77 项定向回归通过；
全量 Rust 965 passed、2 个原有 ignored。格式/Ruff、319 个源文件 strict mypy、函数
文档、Clippy、私有 Rustdoc 与 MkDocs strict 通过。全量 Python 为 3053 passed、0
skipped、485.81 秒，使用真实 Python 3.9 bridge；原结果保留在
`artifact-journal-python-full.xml`。新增实际原生产者门禁进入 CI。

## 继续必需

本包闭合静止 Artifact Snapshot；Archive/Raft Snapshot/online Replica 生产者、
完整内容闭包、原引用释放/GC、typed-map/在线多数派/升级恢复和 N4–N7 继续实施。
存储/进程证明不建立设备停止、跨主机就绪、硬实时、物理连续性或 SoftwareComplete。
