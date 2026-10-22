# Artifact 实际 owner 与显式 retained layout 验收

本包在 `codex/nonrelease-remediation-closure` 上继续 P1 的跨 owner 边界工作，接续
`a2a3bc73d5dec71646ac0d4add72bf4858b077d6`。该前置提交的
[完整远端 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39720079953)已成功，
main 仍保留在原基线。原 provider Replica/Snapshot 生产者不重复实现。

Artifact Store 显式 retained constructor 在原 writer lease 内持久化原 nonce 与真实
目录 inode 的 intent，将 `blobs` 原子迁移为 `objects-v1`，建立旧路径 regular-file
fence，再发布同一 owner。重启只沿用原身份；缺失、冲突、symlink 或陌生目录拒绝。
原 bytes/inode 不改变，staging 和原语义权限保留。配置通过
`ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA=roboguide.artifact-storage/v1` 选择；Replica child
配置验证限定该值，并将其纳入原 application policy digest。

Controller 的 `GET /v1/artifact-store` 在实际 writer ordering 内重新检查实际 TLS
principal，只提供受限运维观察。Viewer 的伪造 admin header 被拒绝。owner 标识软件
backend，不授予读写、Group/tenant 权限，不等于 Node 或设备身份。

恢复 v0.9 显式保存 owner/fence、原 `objects-v1` 和 staging；缺失 mandatory 文件即使
重新固定 manifest digest 也拒绝。原 inode 是历史迁移证据，冷恢复不声称物理连续性。
同一逻辑 backend identity 与真实内容恢复后由实际 Controller 原授权端点独立读取。

## 已验证证据

根目录为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`。以下为实际运行：

```bash
uv run python tools/quality/check_artifact_owner_processes.py \
  --output /data/workspace/roboguide-evidence/nonrelease-closure/p1/artifact-owner-final \
  --skip-build \
  --old-controller /data/workspace/roboguide-evidence/nonrelease-closure/p1/legacy-integration-server-45a8241 \
  --old-recovery-ref a2a3bc73d5dec71646ac0d4add72bf4858b077d6
```

该门禁已通过真实 legacy bytes→retained 迁移、原 inode/owner 保留、实际 mTLS Controller
上传 1 次、恢复后独立读取 2 份原 bytes、Viewer 越权拒绝与缺失身份不重建。旧 Controller
binary `sha256:154f83e414c8ea1015750113bb184911b528ebc0c114264493f1127f5694d461`
因旧目录的文件 fence 退出 1；真实旧恢复工具从上述已提交源码提取，也因该路径不是
目录而在 publication 前拒绝。并非只用新工具选择旧 profile 来替代旧工具执行。

Artifact 存储 22 项测试通过；完整 Rust workspace 为 954 passed、2 ignored，Clippy
all-targets/all-features 与私有 Rustdoc 通过。完整 Python 为 3037 passed，0 skipped，
494.05 秒，使用真实 Python 3.9 bridge interpreter；该全量运行早于下述回执字段修复。
修复后恢复工具 61 项回归和 strict mypy 通过，包含原合同与 retained Artifact 完整
copy、原 owner/fence 缺失、冲突、改 manifest pin 及精确恢复。新增实际零 Node 门禁已纳入 CI。

`retained-replica-certificates/result.json` 保留 3 个真实软件副本的原 mTLS 换证、CA
迁移和原 Controller child wait/transfer 检查。随后
`retained-replica-recovery-02/result.json` 完成显式 v0.9 的 6 个 SQL 数据库、42 张表、
原 revision 10 换证记录及 27120 字节原内容的完整冷恢复；三个恢复 leader 端点独立读取。
Controller binary 的真实 SHA-256 为
`e1e08e081fdfa9fe3bc0070bfb180e490ac95bfdf6a308b8f0e133a41c57dada`，
Replica binary 为 `1b0de892b068284a671debd443d613424ca4cc481a18e70d4693ff305e4bd582`。
全部 owned process 实际回收，0 Node/device/model/simulator。

第一轮 `retained-replica-recovery/` 发现原 v0.8/v0.9 虽复制实际内容，却未写入
`artifact_replica_placements` 恢复回执字段。现按 manifest 中原 `artifact_replica_sets`
声明保留该字段，避免原命令重试丢失位置证据；增加 v0.8/v0.9 回执断言。原失败输出与
已发布回执保持原样，修复后在新目录独立复跑，不改写历史结果。

## 必需后续

本包提供真实内容 owner 与新格式完整恢复的依赖。实际 Catalog/lineage 引用、事件
Archive 的内容闭包、在线 Replica 内容持久化与多数派 ACK、其余 P1–P7 仍继续必需。
存储 owner、同 digest 字节和各原 provider/Node 的副本不能合并为一份保留责任。
这些软件/文件/进程结果均为 0 device、0 model、0 simulator；不宣告 SoftwareComplete。
