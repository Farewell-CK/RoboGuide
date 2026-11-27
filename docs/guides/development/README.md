# 核心开发

先明确职责归属，再沿应用事务追踪完整的命令与事实路径。实现变更应复用已有权威，不增加平行的执行或资源状态表。

[Module map](modules/domain-ports.md) · [Coding standards](coding-standards.md) · [Ownership model](control-ownership-model.md) · [V2 architecture](../../architecture/v2/README.md)

使用仓库固定的 Rust/Python 工具链。先执行改动覆盖的窄检查；公共合同、schema、checkpoint 或派发边界变更需相应集成/恢复门禁。纯文档变更执行文档检查与受影响的打包 runbook 检查。
