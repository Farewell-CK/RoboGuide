# ADR-0150：有界 Node application pipeline

- 状态：Accepted
- 日期：2026-10-22

## 问题

实际双软件 Node/MI/Memory 链路观察到输入排队超过 6 秒，伴随 freshness fence、未派发
slot 被隔离或 Task verifier 到消费时过期。关闭新 Catalog 引用后也可出现。v0.4 reader
虽持续验证 transport，后续应用 loop 却逐条等待 Commit，同一 session 的事实无法充分
进入现有 NodeFactDriver 有界批处理；增加 network worker 不能消除这个串行点。

## 决策

原 registration 必须先独立 durable accept/activate。随后每 session 最多 8 个待完成事实，
按原 validated wire 顺序同步进入应用队列；保留原 receive metadata。每个 application
completion deadline 在送入应用队列前固定，等待/轮询其它事实不能重新计时。既有
Controller writer 按输入次序逐项归约、保存实际中间证据，完成同一原 SQL transaction
后返回各 disposition。registration update 仍是应用 barrier。

transport 只按原顺序消费完成回执、发送 ACK；后项先完成不能越过前项。第一个拒绝、
不可用或超时终止 session，已提交/不确定前缀保留原事实，不因 future cancellation
断言未发生。原 route removal/Unavailable fence 保留；该窗口不是新资源或控制权威。
wire/Node contract、source TTL、lease、Memory issue/receive budgets 和独立 stop proof
不改变，不通过刷新旧证据或延长期限获得通过。

## 验证

合同测试验证窗口上限、按序排队与 ACK、后项先完成不 ACK、拒绝和原 deadline 不续期。
实际文件/MI/Memory/原目标链路验证所有原证明与负向 fault 窗口，再比较输入队列观测。
测试进程不产生设备或物理连续性；短窗口不能代表长期 SLO。
