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

各入口默认同时连接 128，显式范围 1–1024；Node 每连接 stream 默认 16，范围 1–128。
Mission TOML 与各 Local Provider CLI 单独声明有限 transport timeout。TLS/线程前拒绝
超额连接，原 handler 退出后才归还 permit；socket 超时不能取消已提交工作。满载和拒绝
增量告警按[入口容量合同](docs/development/listener-capacity-v0.1.md)处置，原命令保持只读对账。

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

[应用代次升级](docs/development/application-generation-upgrade-v0.1.md)显式使用 process v0.8、parent10 和 recovery v0.25：先滚动基础设施，再收集所有实际成员支持、原 child wait 和完整 predecessor，最后提交独立 activation。失败应用不关闭管理面；观察不重发原命令，显式放弃不能撤销已启用版本，同 schema 修复禁止原/退休代码回退。完整恢复保留原升级、放弃、预算和退出历史，不启动服务。

## 故障处置顺序

保留原日志与 receipt → 检查 exact ID/session/attempt → 判定当前 authority 是否可写 → 核对 State 与 Memory 原期限 → 使用正式只读 observation/reconcile → 满足原 stop/revoke 与 policy 后恢复。禁止以“重新跑一次”消除未知副作用。

显式 scoped typed-map 使用 MissionPlan v0.11、Node v0.14/config v0.15、preparation v0.3、Memory SQL v5 和 recovery v0.26；旧版本不得借用新授权。见[完整生命周期](docs/development/scoped-typed-map-lifecycle-v0.1.md)。

## 原资源预订

recovery-admin 的 `/v1/resource-topology` view 区分声明、原 reservation 与未获证明的物理占用。窗口到期进入既有 whole-Group stop/revoke，原释放门禁通过前保留资源。实际 OS exit 与 Controller stop confirmation 是不同证据。当前 wrapper v60 使用显式 recovery v0.29，保留之前 scoped-map、升级与全部原恢复要求；旧回执保留原版本。

## 经审查的运行协调

按[协调合同](docs/development/execution-coordination-v0.1.md)在候选输出和独立 Review 前冻结完整原 cohort。回执、终态、原进程退出与设备停止分别核对。活动 v0.3 Readiness 策略在撤销和过期后仍保留原保护；恢复前检查 plan-revisions 和原 stop/revoke 证据。

## 跨域事务

按[跨域协调合同](docs/development/federation-coordination-v0.1.md)显式声明 peer、CA/leaf/client 身份文件、domain grant 与时钟上界。回复丢失保留 Unknown，查询原事务，不能换 ID 消除不确定结果。Root 替换会 fence 原 dispatch epoch。冷恢复保留原 goal/clock/intent/decision 表且不启动服务；Secrets 和实际跨主机/物理验证仍是显式部署责任。

复制部署使用显式 `federation` 进程输入，把原 domain、授权文件和可选 root peer profile
交给唯一 owned Controller child。路由只选择真实目标并透传 TLS，一次连接不能重发业务
mutation。原 source 排空/新 epoch 不授予旧事务继续执行的许可。跨域完整静止恢复使用显式
v0.31：按原 profile 区分同编号成员，核对各域完整 Root、活动事务与原 nonce/Prepare/Commit/
release；旧布局保留原格式。凭证是独立显式挂载输入，恢复不安装凭证或启动服务。见
[部署与恢复合同](docs/development/replicated-federation-deployment-v0.1.md)。

## 在线协调归档

使用显式 process v0.9 的 `coordinated_archive_base` 保留原 v0.6/v0.7 profile。global recovery-admin 先查询 `/v1/maintenance/coordinated-archive/operations`，再用新的原 command ID 请求精确 cutoff。重复 ID 只读；Unknown 查询不重发发布，显式 abort 也不补造完成。包是私有控制元数据，Artifact admin 不具有读取权限。parent11 与 recovery v0.30 在发布前校验必需 journal、原 manifest 和实际 qualified CAS 字节；完整恢复不启动服务。见[归档合同](docs/development/coordinated-content-archive-v0.1.md)。
