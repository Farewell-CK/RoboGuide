# ADR-0129：配置驱动的部署拓扑与只读冻结证据

状态：Accepted，当前软件合同与实际零 Node 配置验收已完成。

## 问题

现有 deployment profile 记录了候选拓扑和恢复目标，但没有一个执行入口统一检查原 binary/
配置、持久化唯一 writer、进程/主机/故障域、跨边界 TLS、协议支持与故障验收证据。
散落的当前进程验收不能替代部署输入，文档声明也不能代表实际上线或物理节点存在。

## 决定

新增闭合、版本化的部署 profile，由调用者声明任意实际组件、主机/控制域、持久化路径、
协议/信任连接、RTO/RPO 目标和退出证据。允许零 Node；没有预置机器人、设备、Capability、
Actor placement 或 fake inventory。主机与故障域声明不升级为硬件身份事实。

只读 inspector 验证原文件 digest、真实目录与 inode、每个 store 的唯一 writer、声明图、
跨主机 TLS 与固定协议边界；可选择对明确 endpoint 执行有界 TLS/HTTP GET 探测。
实际 TLS leaf、原 trust/client material、配置和可观察结果随 profile digest 固定。
原私钥只用于建立 TLS，不输出或记录其内容。健康响应只证明当前可达，不证明执行就绪。

冻结 receipt 明确区分声明、实际本机文件检查、网络观察和原验收文件完整性。RTO/RPO 是
目标，不因冻结成为已达指标。硬件/跨主机证据缺失保持 pending，不从同机软件推断。
receipt 不启动服务、不写外部应用、不提交 Mission、不注册 Node、不授权 Control/Memory，
也不以任意 JSON 中的 passed 直接宣告全项目 SoftwareComplete。

后续总验收使用同一配置与固定证据，其他 F4–F10 实现仍必须完成。完整物理 MVP 的历史
Draft/候选评审状态独立，软件合同冻结不能伪造设备验收或发布授权。

闭合合同见 [deployment profile v0.1](../../contracts/deployment/profile-v0.1/README.md)，
原始结果见 [实际配置验收](../development/acceptance/2026-10-21-deployment-profile.md)。
