# RoboGuide

RoboGuide 是 Distributed Embodied AI OS 的工程仓库。当前仓库以
`Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx` 作为架构基线，先建立职责边界和最小可运行骨架，再逐步进入实现验证。

## 当前基线

系统的主闭环是：

`Mission -> Task -> Capability -> Scheduling -> Execution Group -> Coordination -> Runtime -> Physical World -> State/Memory -> Reconciliation`

四个逻辑平面：

1. `mission_intelligence`：理解 Intent，管理 Mission，分解 Task，形成 Task Graph。
2. `control_plane`：能力匹配、Capability + Compute + Space + Time 联合调度、Execution Group、协同和恢复。
3. `state_memory`：提供当前 Reality View 与跨时间的工作/情景/长期记忆。
4. `runtime`：完成能力调用、分布式执行、数据传递、远程计算、设备访问和本地 Runtime 对接。

`Execution Group` 是调度与协作执行之间的一等对象。Global Autonomy 负责
`What / Who / Where / When / Shared Where`，Local Runtime 负责
`Immediate How / Real-time Control / Safety`，高层推理不能直接连接电机控制。

## 仓库结构

```text
.
├── docs/
│   ├── architecture-baseline-v1.1.md
│   └── implementation-backlog.md
├── src/roboguide/
│   ├── core/                   # 跨平面的基线清单与未来共享抽象
│   ├── mission_intelligence/   # Mission / Intelligence Plane
│   ├── control_plane/          # Embodied Control Plane
│   ├── state_memory/           # Embodied State & Memory Plane
│   └── runtime/                # Distributed Embodied Runtime
├── tests/
├── 6610bdc02b402bbfaa494ca8b2d45301.png
└── Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx
```

## 运行自检

项目只依赖 Python 标准库即可运行基线自检：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m roboguide
```

如果需要以可编辑包方式安装：

```bash
python -m pip install -e .
roboguide
```

## 当前明确不冻结的内容

V1.1 是架构与语义基线，不是 API 规范。当前不冻结具体 Schema、通信协议、数据库、进程拆分、高可用/一致性算法、调度优化器、Traffic 冲突算法、Memory 存储实现或节点接入方式。详见
[`docs/implementation-backlog.md`](docs/implementation-backlog.md)。

## 开发原则

- 先保持职责边界，再用实验数据决定实现策略。
- State 表示当前系统掌握的现实；Memory 表示跨时间的上下文、历史和知识。
- 异常必须反馈到 State，并重新进入 Coordination / Scheduling 闭环。
- 本地实时闭环和最终 Safety Veto 不能被全局控制平面替代。
- 任何真实设备控制接入都必须单独经过安全、可观测性和故障恢复验证。
