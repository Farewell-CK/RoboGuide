# 开发与接入

目标是在不修改 RoboGuide core 的情况下，让 Node Service 接入一个已有 Local EAIOS。先完成[快速开始](getting-started.md)，再使用真实后端替换测试配置；不能把示例 Node 当成设备 inventory。

## 接入步骤

1. 明确 LocalSystem、真实 endpoint、语义操作和本地安全责任；从 `config/node.toml` 阅读配置结构。
2. 声明 Capability Profile：exact contract、typed attributes、readiness observation；独立声明 Operation Support 和 private workflow binding。
3. workflow 只能调用部署配置允许的 HTTP、dynamic gRPC 或 MCP endpoint。参数映射传递 semantic objective/parameters，不把厂商 API 变成全局 Mission 语义。
4. 需要状态时，显式配置 State export 的 owner、object、schema、TTL、采样周期和只读 observation。采样失败让旧证据过期，不编造新负值。
5. 需要内容时，声明 Memory provider、scope 与 discover/export/import/availability/use/query 合同；字节传输通过 Artifact 数据面。
6. 运行配置 conformance，再在独立软件部署中测试注册、readiness 变化、Execute/Cancel、断连、重复命令和重启。
7. 真机上线另补设备停止、能力就绪与物理效果证明；软件健康和配置检查不能替代它们。

## 合同与兼容

[参考页](reference.md)生成当前版本。按所需功能显式选择 config/Node Contract；`config/node.toml` 是保留的旧版基础示例，不会因新代码存在就获得 Memory 或安全代际权限。

[Node catalog](https://github.com/Farewell-CK/RoboGuide/tree/main/contracts/node/v0.17)和[设备扩展规范](docs/extensions/device-extension-conformance-v0.2.md)解释具体字段。MCP/HTTP/gRPC 是本地 driver，不改变 Control/Runtime 权威。

## 添加独立验证

先定义 Task expected effect、predicate、来源和 evidence schema，再配置 verifier policy。报告绑定完整当前 attempt、plan digest、tenant/source、freshness 与 finality；需要 CAS proof 时检查实际 bytes。

一次操作返回成功只说明其承诺的本地语义。要求独立验证的任务必须保留 `verifier-evidence`，不能在接入困难时退化成 execution-report。

## 开发验证

Rust 使用 `rust-toolchain.toml`，Python 使用 `pyproject.toml` 与锁文件。基础检查包含 fmt、Clippy、pytest、Ruff、mypy 和函数文档；新增接入还要执行真实部署 Python 的兼容检查。

[模块地图](modules/domain-ports.md)定位代码；[原始扩展与集成材料](archives.md)保留完整命令及历史范围。

## 时空部署约束

全局 recovery-admin 按实际声明资源安装 [resource-topology v0.1](docs/development/resource-topology-v0.1.md)。Space 明确 frame/anchor/边界/clearance；Time 别名共享明确 time domain，窗口只属于本地 Controller 时钟。部署 booking budget 不进入模型 canonical 输出；准入、Bind/Rebind 与每次 outbox 都核对原期限。声明几何不会创建设备 inventory 或实测占用。
