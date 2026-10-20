# ADR-0116：持久副本成员证书轮换

日期：2026-10-20。状态：Accepted；实际软件证据见
[验收记录](../development/acceptance/2026-10-20-replica-certificates.md)。

Controller replica v0.1/v0.2 把每个基础设施成员绑定到一个启动 leaf，替换配置会改变
持久 cluster profile。不能用修改 SQL、关闭精确 pin、把成员替换称作同一身份轮换，
或以预置第二组证书宣称完成通用生命周期。

引入显式 replica v0.3。原配置中的 leaf 只作为不可变 bootstrap 身份；当前可用和已撤销
leaf 由同一个 Raft 日志、事务及快照持久化。基础设施成员身份不变，与具身 Node 无关。
Application batch 保留原编码；独立闭合的 certificate command 不改变应用 revision、事件、
checkpoint、Mission、物理资源或原始时限。旧 profile 不接受新 authority。

轮换先提交 stage，固定 command、原 revision、成员、旧/新 leaf 和实际认证主体。随后部署
原子替换该成员的单个 PEM bundle；服务端、Raft 客户端及子 Controller fence 每次真实连接
读取当前凭证和已提交的当前信任，所有应用字节之前验证精确目标 leaf。旧 writer/错误
profile/缺失或损坏的身份状态拒绝工作，不能退回 bootstrap pins。

retire 前，leader 从所有配置成员的实际 mTLS 连接收集当前 stage 确认，并要求每个成员
实际连接轮换目标、观察新 leaf 和同一 stage。调用者不能提交“已确认”布尔值。原确认
随 retire command 持久化后才撤销旧 leaf；缺少成员、quorum、目标新私钥或原始 stage
均保持 pending。这个有界 profile 为明确的可用性取舍：所有配置成员须参与退休。
证书 validity 在实际 handshake 与排队取得 member writer 后都由标准 rustls verifier
复核实际完整 chain；不把 PKI 时间转换为 State 新鲜度。Append/Vote/snapshot/只读 probes
不等待这个 mutation gate，避免退休等待自己的复制。

每个 ID 的原请求及结果不可变；相同成员换证后可观察原结果，不重做已提交轮换，也不
复活已撤销 leaf。记录有显式容量上限，不能删历史以复用证书。TLS frame 接收后、进入
命令分派前再检查当前 sender；旧 child epoch 仍不能凭换证获得新 leader 权威。

显式 Abort 绑定原 Stage 并永久退役未启用的 new leaf，保留尚未退休的旧 leaf；这是单纯
收窄身份，不能提供虚构新-key ACK 或复活权限。Stage 必须为 Retire/Abort 留一个原日志
容量；命令总界限 512，达到时拒绝新 Stage，历史和退役 leaf 不删除。

CA roots 是部署 TLS 材料，不独立授予成员身份：原子部署 old+new roots 后，逐成员以相同
Stage/新-key TLS/Retire 协议迁移到新 CA 签发的 exact leaf，所有成员迁移后才移除 old
root。每个观察保留实际连接加载的 roots DER digest；只读 endpoint 可核对当前材料。
增加 root 不能绕过 exact member pin，重新加入 old root 不能复活退役 leaf。材料路径、
DNS 和 bootstrap profile 保持，原始 source identity 不重写；材料错误/缺失 fail closed。

跨版本升级、旧 v0.2 数据的显式升级、跨主机恢复及内容一致性仍为后续必须完成的独立
要求。本 ADR 不冒充这些交付；缺设备不影响本软件协议实施。
