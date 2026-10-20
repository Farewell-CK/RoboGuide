# Replica 成员证书轮换 v0.1

[ADR-0116](../../../docs/decisions/0116-replicated-member-certificate-rotation.md)定义
显式 replica config v0.3 的同一基础设施成员 leaf 生命周期。正式操作和失败处置见
[部署合同](../../../docs/development/replica-certificate-rotation-v0.1.md)。

`request.schema.json` 是管理入口的闭合请求；它没有 caller、ACK、confirmed、安全布尔量
或进程选择。实际 mTLS sender 和各成员独立目标连接由服务端捕获，完整原 receipt 随
Raft 事务、快照和重启保留。transport 还执行原 profile、精确 pin、epoch 和 quorum 门禁。

该合同不注册机器人，不授予资源，不改变应用 checkpoint，也不声明 CA 根轮换、二进制
滚动升级、跨主机恢复或设备连续性已完成。
