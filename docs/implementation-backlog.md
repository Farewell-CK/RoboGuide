# Implementation Backlog

以下事项由 V1.1 明确延后到实现、策略或算法阶段。它们不是当前架构缺口，不能在没有实验依据时被默认冻结。

- Capability 的 Schema、类型系统、Contract 字段和版本兼容策略；
- 节点接入采用 Agent、Adapter、SDK、Plugin 还是 ROS Bridge；
- Control Plane 的内部进程划分、Leader Election、高可用和一致性算法；
- State 的数据库、缓存、事件总线和复制方式；
- 现实世界状态冲突采用投票、滤波、因子图或其他融合方法；
- Scheduler 采用规则、启发式、优化求解、拍卖、强化学习或混合策略；
- Execution Group 异常时的局部替换、整体重建、等待、降级或人工接管；
- Traffic 的时空图表示、Reservation 数据结构和冲突求解算法；
- Distributed Data 的传输协议和发现机制；
- Memory 的向量库、图数据库、关系库、对象存储或多层组合；
- Benchmark、SLO、QoS、延迟预算、资源预测和调度成本函数；
- 面向真实硬件的 Safety、权限、心跳、超时和故障恢复验证。

## 判断规则

如果一个问题不解决就无法判断“模块是谁、职责是什么、输入输出是什么、边界在哪里”，它属于架构问题；如果架构已经能承载，只是存在多种实现策略，则先保留为实现决策，使用实验结果再确定。
