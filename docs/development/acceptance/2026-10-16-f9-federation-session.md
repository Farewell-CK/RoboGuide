# F9 跨域 Federation prepare/commit 软件合同

状态：Control 侧软件合同通过；跨主机网络、远端域实现、证书/租约路由和物理连续性仍未
宣称完成。

Control 现在持久化 `FederationSession`：transaction、source/target domain、source epoch、
accepted plan digest、ownership digest、nonrenewable deadline 和 `Prepared/Committed/Aborted`
state 都是闭合字段。Prepare 对相同完整身份幂等，对 identity 冲突拒绝；Commit 必须匹配
原 epoch、plan/ownership digest 且在原 deadline 内；Abort 只能结束仍为 Prepared 的 session。
只有 current committed session 返回 `federation_dispatch_ready=true`，并且调用方仍需执行本域
Control/Runtime/Node 的独立 dispatch gate。Checkpoint restore 会保留并校验这些 session，
重启不会续期 deadline。

这层不创建第二套资源 authority、不选择 Node、不自动发送 Execute，也不把两个本地 Control
副本当成跨域部署。实际跨域 transport、远端 domain handler、证书轮换和 quorum/lease 组合
仍是后续部署工作。

## 软件验证

`cargo test --locked -p control federation_commit_is_epoch_and_digest_bound_and_checkpointed`
通过：覆盖 prepare 幂等、旧 epoch 拒绝、digest 绑定、commit、checkpoint restore 和 deadline
失效；整体 Control 回归保持通过。
