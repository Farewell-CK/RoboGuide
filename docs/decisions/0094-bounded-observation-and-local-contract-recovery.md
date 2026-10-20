# ADR-0094：有界主动观测、执行契约恢复与证据封口

- 状态：Implemented on the review branch; physical release gate pending
- 日期：2026-10-13
- 衔接：ADR-0044、ADR-0048、ADR-0069、ADR-0074、ADR-0093

## 问题

读取缓存只能回答当前观测，不会主动改变视角；本地导航完成也不证明视觉目标已经被检测。
此外，final-world verifier 需要全部执行 slots 完成，而 verifier-backed Task 在
`AwaitingSatisfaction` 保留 endpoint，二者可能形成无法释放的资源等待。
异常后的原始数值序列化及终态发布时序也可能造成执行已发生、官方结果却未封口的归档缺口。

## 决策

1. 保留 `observation.verify@v1` 的只读含义。新增中立的 `observation.acquire@v1`：
   通过显式、受限的本地定位与传感能力获取指定 condition。当前 Habitat profile 仅支持
   `detected(entity-ref)`，允许原始导航工具接近该实体、wait 和已有 peer 通信；不支持替换
   实体、pick/place 或无界搜索。缓存 true 才构成本地 acquisition 完成；false/unknown
   在预算内继续，预算或 episode 结束时保留真实失败。
2. 新能力、已有缓存能力通过 Catalog 的 typed scalar attributes 区分 acquisition mode、
   modality、是否允许移动和 cycle 上限。未声明的 FOV、gimbal、可见性和通行能力保持
   unknown，不能由成功率或机器人名称补造。
3. planning world evidence v0.4 可携带 reset 后的实体坐标，使用具名场景 frame、Y-up 和
   毫米单位，绑定 run/episode/scene/dataset/source/digest。它不提供 Node inventory、
   起点分配或路线可达性，不改变 MissionPlan 或官方判定。旧 v0.1–v0.3 保持可读。
4. feasibility v0.6 显式声明 `after-all-session-slots` 的 verifier publication、容量一的
   endpoint 和 `after-task-satisfaction` 的 release。准入只拒绝可证明无法同时容纳的
   verifier-retained slots，包括同一 Actor 重复占用及候选集合的容量不足。普通 execution-report
   前驱仍可先满足并复用 endpoint；这里不建立 distinct-physical-entity 的任务语义，也不
   选择 Node 或提交资源。其他时序问题仍需实际调度证据，不能声称完整 DAG 可执行性证明。
5. Contract Guard 默认保持一次调用后拒绝。显式启用的 recovery 使用同一模型和工具契约，
   在 CrabAgent dispatch 前附上 exact contract、原始拒绝动作、错误、已观测的搬运阶段及
   剩余预算，要求重新选择。每个绑定 attempt 共用最多三次、不能按 step 续期的预算；
   Provider/基础设施异常不重试。Guard 不编辑动作、不推进 pick/place phase、不修改 Task，
   更不具有 Control reassignment 或 Mission replanning 权限。
6. execution failure 与 archival failure 分开。严格 JSON writer 保留有限数值的值和类型；
   不支持的对象不被字符串化，原始异常继续传播。live producer 的 closure route 和原子
   closure artifact 记录实际 summary/verifier/session 写入结果。Runner 使用有界、具名
   Mission 的 publication barrier，替代固定 sleep。未封口的观察保留在独立 harness artifact，
   采集命令失败；不改写 SUT failure、Formal admission 或官方 benchmark outcome。
7. B1 deployment v0.4 冻结 acquisition/recovery 选项；Local How v0.10 披露实际子进程
   加载的选项。此前 profiles 保持显式兼容。受控 arm 的模型输入/Local How 差异应在
   对照 manifest 中列明，不声称与原生 EMOS 完全等价。
8. Local How v0.11 为开启 acquisition 的部署显式披露
   `bounded-visual-feedback/v0.1`。每个原始 attempt 将已有 reset/step 缓存的
   true/false/unavailable、来源/时刻、精确 condition/world/attempt 与原始剩余步数交给下一次
   Stage2 决策；不额外 render、调用谓词或延长预算。已为 true 的初始缓存直接完成本地
   acquisition，不多走一步去破坏现有状态。它不锁存终态、不代替官方联合 verifier。
9. 该本地 profile 公开将模型选择的 wait（以及原始消息派发后的 wait）映射为最多16个
   simulator steps、且不超过原始剩余 acquisition 预算的暂停，复用原始 WaitSkillPolicy。
   raw tool call 保留，模型在调用前看到实际持续时间，证据同时记录原始500步映射和实际
   本地派发。没有选择替代工具、改目标或修改 vendor 源码；未启用 acquisition 的导航、
   搬运和 idle 保持原映射。v0.9/v0.10 历史声明继续按其原合同读取，不能冒充 v0.11。
10. Local How v0.12 增加 `fresh-acquisition-navigation-command/v0.1`。原始 Oracle 动作
    会抑制同一已完成目标，且把完成信号重置为 false；新 acquisition 的实际工具调用不得
    继承此命令 latch。Guard 与原始工具映射通过后，仅清除自身动作的两个完成标记，由
    下一次原始动作重新计算到达。保留目标缓存、PathFinder、原始技能与总预算；不写传感器
    true、不更改物理状态或官方判定，也不触碰 peer。运行前检查实际动作接口，未知实现
    fail closed。原始 EMOS 文件不变；这是明确披露的 Local How 生命周期差异。

11. 默认关闭的物理诊断增加有界缓存/语义像素/相机几何对照。当前跨 agent 缓存并集
    与自身 detector 分开记录，不额外执行官方谓词，不锁存历史检测。采集和序列化故障
    不改变执行，异常终态补采失败仍 flush 原有记录，原异常继续传播。

## 软件与物理验证边界

确定性测试覆盖拒绝动作不 dispatch、恢复预算不续期、Provider 错误不重试、导航完成但检测
false、缓存 unavailable、有限预算以及 publication 延迟/失败。软件测试不证明模型遵循率、
摄像头可见性、导航/搬运可行性或官方成功率。新物理验证使用独立冻结代码和新目录；原生
EMOS 源码与正在执行的历史 batch 不变。live profile 不支持重启后恢复物理连续执行，因此
本地恢复预算也不授权重启后重复同一 attempt。
