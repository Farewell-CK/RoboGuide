# ADR-0138：leader transfer 前原 Controller 子进程排空

状态：Accepted；软件进程证明始终与 Node/设备停止分离。

## 决定

真实 supervisor 在生成每个 owned Controller 前，持久化原 operation instance、supervisor
instance 和 epoch；spawn 后登记实际 PID/内核 boot/start tick，实际 `Child.wait` 后登记
原 exit code/signal。spawn 未对账、原进程仍活跃、重启失去原 wait handle 时均保持 Unknown，
不能以 PID 不存在、terminal、kill 已发或 Raft follower 代替原 wait。历史有界且不覆盖。

source transfer 先冻结当前原 child identity 并提交 intent，立刻 fence 应用 Commit/命令；
supervisor 停止且 wait 原 child，并持久化原退出证明。只有完整原证明、原 epoch quorum
与原八秒 deadline 仍成立，才发送显式 `TransferLeadershipV3`。未管理 application child 的
嵌入式 Replica 只声明实际空进程状态；启用过 supervisor 的 store 不能回退这个路径。
配置型和 admin 型 transfer 经过同一个 gate。target 核对实际认证 source、原 epoch 和
完整 proof，并在原接收相对预算内触发选举；排队不续期。

新 source/drain 字段受 storage v3 与 recovery v0.7 保护；旧 binary 拒绝。原 v2 transfer
原值保留，缺少本版本独立 drain 证明的 legacy 记录保留 Unknown，不能生成强完成证明。
旧格式已存在应用 checkpoint、却没有原 child journal 时，原进程连续性保持 Unknown；
迁移不能把这个缺口解释为空进程，也不能自动启动新 child。需要独立、显式的旧部署
排空/迁移证据，此边界在完整旧版本受控升级验收前保持待完成。
应用 checkpoint/Node 合同不变化。恢复不启动服务、续期或重复原 election。

实际三 supervisor/Controller/SQLite/mTLS gate 必须证明原 source PID 已被原 supervisor
wait，完整 proof 早于 target accepted，重启/回包丢失与数据恢复不丢原证明。本合同证明
基础设施进程退出，不能证明机器人、Local EAIOS 工作或设备已安全停止。
