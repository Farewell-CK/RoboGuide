# 文档图片资源

当前代码架构改用可维护的 Mermaid 源码（2026-10-22），见
[架构图册与代码依据](../architecture/v2/current-architecture.md)。

- [roboguide-current-architecture.mmd](roboguide-current-architecture.mmd)：中文总体图单一源码。
- [roboguide-current-architecture-english.mmd](roboguide-current-architecture-english.mmd)：英文总体图单一源码。
- README、V2 入口、网站导览的标记区须与源码一致；`website/check_site.py` 检查漂移。

2026-10-20 当前部署 Mermaid 同步 ADR-0119 的 Operator TLS 前连接预算、完整请求 poll
handoff 和 response I/O 期限；实际 writer/TLS witness/Commit 权威保留。

本目录集中保存出现在 README、架构说明和后续设计文档中的图片资源。

2026-10-20 当前部署 Mermaid 同步 ADR-0118 同步事务/network worker 边界，以及 transfer
所有退出路径恢复原 Raft timers；原资源/证书/预算权威与物理证据边界保持。

2026-10-20 当前部署 Mermaid 同步 ADR-0117 全 supervisor/Raft/child/Artifact 静止恢复，
显式新 CAS placement 与原 SQL/ACL/预算分开；不绘制为在线跨主机或物理连续性。

2026-10-20 当前部署 Mermaid 同步 ADR-0116 的 Raft 基础设施证书 authority、实际 TLS
确认及独立应用权威；证书不绘制成 Node、资源或设备证明。

2026-10-20 当前部署 Mermaid 同步 ADR-0114/0115：SQL 故障写入禁用、原 checkpoint/
journal probe、专用 metrics-reader、wrapper v42 与真实监控通知链路。观察不授予恢复或
设备安全权限，概念 PNG 继续保留其基线含义。

## 命名约定

- 使用能够表达内容的英文小写文件名；
- 使用连字符分隔单词；
- 对应版本写入文件名，例如 `architecture-v1.1.png`；
- 文档引用使用相对路径，避免依赖根目录中的随机文件名；
- 新增图片时同步在使用它的文档中写明图片用途和来源。

当前图片：

- `roboguide-v2-overall-architecture.png`：历史 V2 概念快照，已嵌入原始 V2 DOCX；当前实现使用上方 Mermaid；
- `distributed-embodied-ai-os-architecture-v1.1.png`：历史 Distributed Embodied AI OS 总体架构 V1.1。

2026-10-20 当前部署 Mermaid 另同步 ADR-0112/0113：原 provider tombstone/reclamation、
Node journal v13 唯一 writer，以及完整 SQL/CAS 的静止恢复路径；不将本地删除或恢复包
绘制成全局内容一致性或物理连续性已完成。

2026-10-19 当前部署视图增加 ADR-0108 双 Group Memory handoff：原成员/attempt/期限、完整
stop/revoke、immutable child lineage、真实接收准备与显式 commit 的关系见
[implementation-topology](../architecture/v2/implementation-topology.md)。该 Mermaid 为当前软件
调用链，既有概念 PNG 保留其基线含义；软件 assurance 不宣称物理交接。

2026-10-20 当前部署 Mermaid 同步 ADR-0111：独立 generation/Memory wire、动态成员与
leader transfer、保留交接 ownership 的失败收尾和 Artifact 应用 writer lease。概念 PNG
继续保持 V2 基线，不把这些软件接线画成设备或完整 HA 数据面已经验收。
