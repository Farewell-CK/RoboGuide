# F5 动态成员与 Leader Transfer 软件验收

状态：软件切片通过；跨站点网络 SLA、证书轮换运维、内容数据面连续性和物理连续性仍待独立
部署证据。

复制 profile 从静态三 voting members 扩展为预注册成员集合：v0.2 配置显式列出完整 member
identity/address 与 `initial_voters`。未在初始 voter 集合中的成员只能先作为 learner；leader
在当前 epoch 和 quorum fence 通过后调用 `add_learner`，等待追平，再通过 OpenRaft joint
consensus 提升或移除 voter。未知成员、空 voter 集、重复 endpoint、旧 epoch 和不在当前成员
集合的 envelope 都 fail closed。`retain` 只保留软件 learner 历史，不赋予资源或设备身份。

Leader transfer 是一个受约束的控制面 handoff：管理客户端先读取 leader epoch，旧 leader
核对目标 voter 已追平当前 log，暂停自身 Raft heartbeat/election，向目标发送绑定原 epoch 的
transfer request；目标只在观察到该 source leader/term、自己属于当前 voter 且 log 不落后时触发
一次 election。源端等待目标实际成为新 term 的 leader，超时则在源仍是旧 leader 时恢复 heartbeat
与 election。Supervisor 在发起 transfer 前关闭自己拥有的 Controller child，epoch 变化后旧
child 的发送 fence 立即失效。receipt 的 `requested` 只记录软件请求，目标 epoch 观察才表示
控制面切换完成；没有物理停止或设备连续性含义。

管理操作只通过当前成员的私有 replica envelope 进入：`ReplicaAdminClient` 绑定完整 cluster
profile、member identity、shared secret/mTLS（若启用）和 leader endpoint；操作带当前 epoch，
没有独立身份的任意 loopback HTTP 管理入口。CLI 入口为：

```bash
controller-replica --admin <process-config.json> --leader <id> --membership
controller-replica --admin <process-config.json> --leader <id> --add-learner <id>
controller-replica --admin <process-config.json> --leader <id> --change-voters 1,2,3,4 --retain
controller-replica --admin <process-config.json> --leader <id> \
  --transfer-leader <id> --transfer-id <stable-id>
```

## 实际进程证据

命令：

```bash
uv run python tools/quality/check_controller_membership_processes.py \
  --output /tmp/rg-replica-membership-20261016-a --skip-build
```

结果：`/tmp/rg-replica-membership-20261016-a/result.json`。

四个真实 `controller-replica` supervisor 进程通过了：初始 1/2/3 voter、4 learner catch-up、
joint add 得到 `[1,2,3,4]`、从 member 1 到 member 4 的实际新 term transfer，以及 joint remove
得到 `[2,3,4]`。本次 `devices=0`、`models=0`、`simulators=0`；普通 Controller child 只作为
supervisor ownership/fence 的软件进程存在，没有 Node、Local EAIOS、模型或实验工作负载。

同一轮 `controller-replication` Rust 测试还覆盖了四成员 learner/joint consensus、目标 voter
追平检查、旧 epoch 拒绝和 transfer 超时恢复；二进制 schema 为
`roboguide.controller-checkpoint/v37`。

## 未关闭边界

成员地址仍必须预注册，尚未做任意新主机的在线证书 enrollment/rotation；当前 profile 不复制
Artifact/Memory 内容，不建立跨站点 archive 共识，也不提供硬件 attestation、物理 stop 或
执行连续性。quorum 丢失仍 fail closed，不允许强制接管。
