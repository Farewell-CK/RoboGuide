# E2 双 Dog 接替扩展场景

这是 COHERENT 官方 `env4/task17` 之外的扩展场景，用来验证 Dog-A 故障后由已注册的
Dog-B 接替。它不能用于官方 100-task 成功率，也不能把第二个 Node 名称当成第二个实体。

`registry.json` 是部署拓扑的输入证据：四个物理实体分别由四个独立 Node 路由，符合当前
`one-routable-entity-per-node` 限制。实际运行前还必须提供两个独立 Dog 的机器人状态、
能力、操作支持、初始位置和 COHERENT 场景状态；本文件不会伪造这些运行证据。

实验顺序固定为：同场景无故障 F0、Dog-A 同节点恢复、Dog-B 跨实体接替。每次都单独保存
registry revision、Node registration、旧 attempt 的 stop proof、Actor takeover authorization、
Match/Schedule/Proposal/Commit/Rebind 事件和最终目标检查。适配器不得改写已接受的
agent-specific 操作；双 Dog 实验必须使用可由绑定实体执行的语义操作合同。
