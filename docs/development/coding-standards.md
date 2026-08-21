# 编码规范

这些规则适用于手写的生产代码、测试、示例和内部辅助代码。生成代码必须隔离，
并明确标记来源。

## 1. 函数文档

每个 Rust `fn` 以及每个 Python `def` 或 `async def` 都必须有文档注释或
docstring，包括私有函数、方法、测试辅助函数、Fixture 和构造函数。

文档必须说明：

- 函数的职责以及重要输入/输出；
- 状态变化、外部影响或并发假设；
- 重要不变量和拒绝条件；
- 适用时的错误、Panic 条件或安全要求。

不要写只重复函数名的注释。行为变化必须在同一变更中同步更新文档。模块还必须
使用 Rust `//!` 注释或 Python 模块 docstring 说明自身的职责边界。

## 2. Rust 规则

- 使用仓库固定的 stable Toolchain 和 Edition；
- 使用 `rustfmt` 格式化，并以禁止 Warning 的方式运行 Clippy；
- Crate 根文件使用 `#![deny(missing_docs)]`、
  `#![deny(clippy::missing_docs_in_private_items)]`，默认使用
  `#![forbid(unsafe_code)]`；
- 使用 Typed ID 和 Value Object，不要传递含义无关的 `String`；
- 将生命周期变化建模为显式状态转换；无效转换返回类型化错误，不能静默修改状态；
- 生产代码不得使用 `unwrap()` 或 `expect()`，除非它对应有文档说明的进程启动不变量；
  测试可以为了设置清晰而使用它们；
- Library 暴露领域错误枚举；`anyhow` 风格的上下文只属于应用和 Adapter 边界；
- 阻塞工作不能运行在异步 Executor 线程上。取消、超时和关闭行为必须明确；
- 新依赖必须说明用途，且不能重复已有能力。

Rust 必须通过以下检查：

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace
RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps
```

## 3. Python 规则

- 所有函数、方法、参数和返回值都必须有类型标注；
- 统一使用 Google 风格 docstring；
- 使用 Ruff 格式化和 Lint，并以 strict 模式进行类型检查；
- 禁止裸 `except`、可变默认参数、Wildcard Import、隐藏的模块级运行时状态和无界后台任务；
- 模型和仿真器 SDK 对象只能留在 Adapter 内部。核心合同对象必须可序列化，且独立于 SDK；
- 网络、模型、时钟和仿真器访问必须可注入，以便测试；
- 依赖和工具版本声明在 `pyproject.toml` 中，并由 Bootstrap 变更锁定；不得依赖全局安装的包；
- Python 命令使用仓库的 uv 环境，例如 `uv run python ...` 和 `uv run pytest ...`。

Python 包创建后必须通过：

```bash
uv run ruff format --check mission tools/quality
uv run ruff check mission tools/quality
uv run python tools/quality/check_python_function_docs.py mission tools/quality
uv run mypy --strict mission/src mission/tests tools/quality
uv run pytest -q
```

启用 Ruff 的 pydocstyle `D` 规则，并使用 Google Convention。这些规则覆盖公共
定义，但不能保证以下划线开头的私有函数有文档。因此，第一份 Python Scaffold
必须在 `tools/quality/` 下加入 AST 检查，拒绝所有没有文档的 `def` 和 `async def`，
包括私有函数和测试函数。抑制检查必须写明行内原因。

## 4. 命名与数据

- Rust Package Directory 在 `core/` 和 `apps/` 下使用简短的职责名称；Rust Module
  和 Python Package 使用 `snake_case`；
- 类型和状态名称使用 V2 的领域语言。除非进一步限定职责，否则避免使用通用的
  `Manager`、`Util`、`Common` 或 `Helper`；
- Boolean 名称使用谓词形式，例如 `is_committed` 或 `can_execute`；
- 单位写入名称或类型，例如 `timeout_ms`、`distance_m`、`TimestampUtc`；
- 配置必须显式声明、启动时校验，且不包含凭据；
- 日志使用结构化格式，并在可用时包含 Operation、Node、Task、Group、Correlation
  和 Error ID。

## 5. 测试标准

- Unit Test 覆盖每个生命周期转换、不变量和错误分支；
- Port 实现共享 Contract Test；
- Integration Test 使用 Fake Nodes 和 Virtual Clock，禁止使用任意 Sleep 等待；
- System Test 验证可观察的事件轨迹，而不是私有实现细节；
- 故障注入覆盖 Heartbeat 过期、证据过期、Capability 降级、Reservation 冲突、
  Invocation 失败和恢复升级；
- 关键状态机转换要求完整转换覆盖。Workspace 的行覆盖率下限将在第一份可测量
  Scaffold 之后设定，且不能替代面向行为的断言。

依赖 Isaac Sim、真实机器人、外部模型或网络的测试属于 Adapter/System Test，必须
单独标记。核心测试保持离线和确定性。

## 6. 评审标准

Pull Request 不能只满足“能够编译”。它必须说明架构边界，记录每个函数，包含
故障路径测试，通过所有质量门槛，且不包含无关重构。`TODO` 和 `FIXME` 必须关联
已跟踪的 Issue 或 Decision ID。
