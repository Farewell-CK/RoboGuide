# ADR-0103：入口命令恢复与 Node 身份生命周期

状态：Accepted；实现中。继承全部非发布软件整改计划，未具备设备验证证据。

## 决策

Mission HTTP 命令的证书主体、tenant、method、path、幂等键和请求 digest 是独立于
Request 的不可变身份。每次 Request 保存必须在同一 SQL 事务绑定当前命令及完整进展快照。
成功响应持久化后才返回。中断命令的公开 reconciliation 只读取既有 Request/Controller
接纳证据，绝不再次运行模型、创建 Request 或重新提交 Controller POST；恢复响应明确区分
已知响应、仍在执行、已中断且状态可查询、旧记录证据不足。相同键不能成为另一次执行。
SQLite Mission Service 的部署 profile 是本地单 writer，HTTP 服务持有进程锁；多个线程共享
活动命令登记。重启不会把不确定副作用改成未执行。

Node 身份由部署维护的持久注册表管理，区分 principal、tenant、control domain、Node 与
PhysicalEntity。所有变更绑定期望 revision，并经管理证书授权及应用事务提交。证书轮换
采用准备、持证激活、受限重叠及撤销；撤销和到期拒绝既有连接的后续消息与命令，不能仅
在 Hello 检查。Integration 只消费提交后注册表投影；Control 保留既有所有权并将失效
session 交给既有恢复流程。旧静态 leaf 映射作为显式兼容 profile，不能声称有动态身份生命周期。

硬件证明由可配置 verifier 验证 challenge、可信 attestation key、签名、测量政策和过期时间，
保存可追溯验证报告及 assurance。软件签名、测试证书和进程结果不能升级为硬件真实性证据；
缺硬件背书时保持 HardwareEvidencePending。模型、State 内容或客户端头部均无注册授权。
