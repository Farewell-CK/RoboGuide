# ADR-0058: Local EAIOS 导航进度观测

- Status: Implemented — default off; automatic recovery is not enabled
- Date: 2026-10-01

ADR-0056 的通用进度接口需要真实本地操作量度，不能把心跳或 simulator step 伪装成
任务进展。Habitat adapter 新增可选 `--progress-directory`，在现有 reset/step 返回后
读取既定目标实体与当前 base 的三维位置；不执行动作、路径查询、谓词计算或 RNG 调用。
原始 EMOS 模型、技能、完成条件和官方评测不变。

## 本地量度

navigation stage 的 `completed_units` 表示相对该 stage 初次观测，历史最佳欧氏距离的
改善量，单位为整厘米。远离目标不减少 counter，也不被计为进展。技能切换、目标物理
位置变化或几何可用性变化产生新 `stage_epoch`，允许重新建立量度基线。`wait` 为
`waiting` 且没有 counter；不支持的技能或读取失败为 `unavailable`，不推断 `blocked`。
尚未启动的 Accepted assignment 表示 deployment wait，不是导航停滞。

该量度不是 geodesic 路径进度、机器人到达、PDDL truth 或 Task satisfaction。必要绕行
也可能暂时没有改善；因此 `Stalled` 只是调用者明确给定时间窗的观测分类，不是自动停止
授权。没有实测误报边界之前，不启用进度触发的恢复。

## 边界与传输

每 endpoint 最多每 250 ms 读取/替换一个最多 4096 bytes 的文件，没有每步历史缓冲或
不断增长的轨迹文件。采集/编码/写盘异常隔离；关闭时不读取仿真状态或创建输出。
Local snapshot schema 为 `roboguide.habitat-navigation-progress/v0.1`，记录原技能、
量度单位与本机 monotonic producer time。它不是分布式时间协议。

HTTP `GET /v1/executions/progress` 仅从固定本地目录读取现有文件，向注册的 operation
owner State export 投影封闭 `roboguide.execution-progress/v0.1`。identity 必须是 intact
invocation 中的 `attempt_id`；本地 `habitat-*` handle 不能替代 Runtime physical attempt。
operation、destination、agent、当前 active row 均须匹配。缺少 attempt、错 identity、
非法/过大/缺失文件、future timestamp 或 producer age 达 2000 ms 都不输出工作 sample。
重复 HTTP 读取不更新 producer time；Node export 的 1500 ms receive-relative TTL 仍由
Controller 独立执行。采集故障最多经过 producer expiry 加 State TTL 后成为 Unknown，
不能通过持续轮询旧文件宣称物理观测一直有效。

B1 当前 Node 模板声明固定 owner-qualified export；只有
`ROBOGUIDE_B1_EXECUTION_PROGRESS=1` 才开启本地量度。关闭时 export 返回空 batch。
进度不进入 MI、Formal admission、Mission satisfaction 或 benchmark outcome。

## 停止与恢复限制

进度不是停止证明。已有 Cancel facade 只持久化取消意图，backend 真正退出动作循环后
才返回 `Cancelled`，由 Node/Runtime 原终态路径记录；进度文件不授权资源释放。
长模型调用期间无法立即确认停止，仍须等待，不能因观测过期而重试。

shared-world 两 endpoint 共用一个物理步进循环，任一取消会结束整个联合 segment；
保留此前真实本地完成结果，其余 endpoint 报告取消。它不是可独立停止、互不影响的
Role recovery deployment。其 consumed episode/session 也没有提供通用中断后重试契约。
不得根据新增 observer 自动调用单 Role recovery 或假定可以在旧世界续跑。

离线回归分别验证量度与 freshness、真实 HTTP payload、原始动作/终态等价性及
failure isolation。Provider-free direct-Oracle 组件预检可验证实际 reset、进度沿
Node Protocol 到达 Controller、取消意图与停止事实；这不是 production MI B1 或
配对 benchmark，不能据此声明 shared-world 自动恢复已验证。
