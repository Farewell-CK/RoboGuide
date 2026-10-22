# 部署与运维

先声明实际拓扑，再启动服务。默认允许零 Node；静态示例没有权力创建真实 inventory。

## 部署前核对

- 区分 component、process group、Host 和 control domain；同进程组件共享真实 binary/host/domain。
- 固定 binary、config、合同支持、SQL/CAS/store owner 和实际 TLS leaf；凭证只引用本地文件，不写进文档。
- 为每个故障定义 affected component、保守 fence、RTO/RPO 目标和退出依据；目标不是实测结果。

```bash
python tools/deployment/deployment_profile.py   --profile /absolute/profile.json   --output /absolute/new-private-receipt.json
```

这是输入与本地文件检查。仅当 profile 指向你自己的服务时添加 `--probe`，执行只读 TLS/GET；不 Register、不 Execute。详见[部署 inspector](tools/deployment/index.md)。

## 健康、写入与告警

健康、readiness、metrics 与业务满足分开。write-disabled 后按已提交视图排查，保留原 generation、预算与 ownership；恢复探针由 recovery-admin 在原 writer 中验证，不手工清除 SQL fence。

Prometheus/Alertmanager 配置来自显式 endpoints/TLS/budgets。短窗口 firing/resolved 证明软件路径，不证明长期 SLO 或人工响应。依据[观测合同](docs/development/observability-v0.1.md)部署，并检查队列/drop coverage。

## 静止备份与恢复

使用闭合 recovery profile，纳入原 Controller、Node、Request、runtime-MI、Memory 四库、Replica 与声明内容。实际 writer/inode leases 必须都释放后才可读取源。

```bash
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery backup   --profile /absolute/recovery-profile.json --destination /absolute/new-bundle   --command-id original-backup-id
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery verify   --bundle /absolute/new-bundle --manifest-digest sha256:PINNED_MANIFEST_DIGEST
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery restore   --bundle /absolute/new-bundle --manifest-digest sha256:PINNED_MANIFEST_DIGEST   --destination /absolute/new-root --command-id original-restore-id
```

这些是参数模板，不是已执行的用户部署命令。将 manifest digest 独立保存；输出必须是新目录。恢复保留 Unknown、原预算、全部历史和 active refs/CAS 一致性，不启动服务、不发放新 use permission。详见[恢复工具原文](https://github.com/Farewell-CK/RoboGuide/blob/main/apps/recovery-tool/README.md)。

## 副本与证书

显式副本 profile 下核对 quorum/epoch、实际成员 leaf 和原 child spawn/PID/boot/start tick/wait journal。leader transfer 先 fence source 并确认原 Controller child exit，再发送原 V3 intent；响应丢失保持 Unknown，只读观察不重复选举。

证书轮换、binary/profile 升级、冷恢复、在线内容恢复是不同流程。不要因 metadata quorum 成立就声称 CAS 或设备连续性成立。

## 故障处置顺序

保留原日志与 receipt → 检查 exact ID/session/attempt → 判定当前 authority 是否可写 → 核对 State 与 Memory 原期限 → 使用正式只读 observation/reconcile → 满足原 stop/revoke 与 policy 后恢复。禁止以“重新跑一次”消除未知副作用。
