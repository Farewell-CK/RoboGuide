# ADR-0136：成员本地 leader transfer、不可重发与独立 quorum 观察

状态：Accepted；修正 ADR-0131 的 snapshot 与完成条件，完整 source-child drain 独立验收。

## 决定

transfer 是成员本地基础设施操作，不能混入 Raft 状态机 snapshot。接收 snapshot 保留
本成员完整 transfer 历史；原 snapshot 的可选 transfer 仅兼容读取，不能授予或删除本地
hold。每个原 ID、source epoch、target、log 条件不可改写，最多保留 512 项；满后拒绝新
操作，不删除历史。同 ID 观察不能重复发送 election，未决操作不能被新 ID 覆盖。

只有目标在新 epoch 下实际完成 linearizable quorum 检查后，才能记录带 exact epoch 的
completed。源经原目标的实际认证只读请求取得同一原操作的完整证明，才能解除 hold。
RPC 错误、丢失回包、取消或 effect deadline 均不能推导 rejected；保持 Unknown/原意图。
源在原意图提交后立即拒绝其 Controller 的 fence/commit，恢复不续发原请求或延长八秒
effect budget。读取既有完成证据不重新触发 election。

新 target command 使用显式 TransferLeadershipV2；旧 target 无法消费新权威，当前 target
也不执行旧 command。SQLite storage v2 固化原 history、保留原 v1 物理表与只读旧 view，
marker/user_version=2 使 v1 binary 拒绝打开。v1 单条记录原值保留为历史，新操作视为
Unknown，不能从先前弱完成条件发明强证明。恢复 profile v0.6 显式拥有 v2 的完整
inode/原格式边界；此前 profile 保留其语义，不能把新 store 重标为旧格式。

本决定修复软件运维记录与共识权威的混用；真正 source-child stop/wait、在线内容、跨域
恢复仍须完整进程证据，不能从 Raft role、terminal 状态或一个 passed 字段推导设备停止。
