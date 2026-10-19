# 文档图片资源

本目录集中保存出现在 README、架构说明和后续设计文档中的图片资源。

## 命名约定

- 使用能够表达内容的英文小写文件名；
- 使用连字符分隔单词；
- 对应版本写入文件名，例如 `architecture-v1.1.png`；
- 文档引用使用相对路径，避免依赖根目录中的随机文件名；
- 新增图片时同步在使用它的文档中写明图片用途和来源。

当前图片：

- `roboguide-v2-overall-architecture.png`：当前 RoboGuide V2 总体架构图，已嵌入 V2 DOCX；
- `distributed-embodied-ai-os-architecture-v1.1.png`：历史 Distributed Embodied AI OS 总体架构 V1.1。

2026-10-19 当前部署视图增加 ADR-0108 双 Group Memory handoff：原成员/attempt/期限、完整
stop/revoke、immutable child lineage、真实接收准备与显式 commit 的关系见
[implementation-topology](../architecture/v2/implementation-topology.md)。该 Mermaid 为当前软件
调用链，既有概念 PNG 保留其基线含义；软件 assurance 不宣称物理交接。

2026-10-20 当前部署 Mermaid 同步 ADR-0111：独立 generation/Memory wire、动态成员与
leader transfer、保留交接 ownership 的失败收尾和 Artifact 应用 writer lease。概念 PNG
继续保持 V2 基线，不把这些软件接线画成设备或完整 HA 数据面已经验收。
