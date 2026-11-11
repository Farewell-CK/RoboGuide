# ADR-0248：独立人工现场到达验证源的正式登记

状态：代码、隔离软件及现场登记通过；真实到达效果未验收。日期：2026-11-11。

## 决定

固定命名目标巡检复用现有 Control semantic-verifier registry、通用 verifier-evidence
入口和 Orchestration Task satisfaction。不增加 Control 规则、运动权限或常驻进程。
本轮生产者是一次性人工现场确认发布器：现场人员明确报告指定狗已到指定目标并停稳，
发布器核对已接纳的原计划、当前完整 role/attempt 集合与固定实体/Node/目标谓词后，
使用独立受限 mTLS 身份发布证据。它不自主测量位置、速度或停稳窗口。

源登记包含真实 producer principal、明确租户、原 verifier contract、精确目标谓词、
revision 和五秒有效期。管理员通过原 registry writer 带 command_id、expected_revision
提交并保留原回执；单独增加 JSON 模板、能力名称或一个全权管理员脚本都不算完成登记。
operator-authorization v0.3 的 verifier 角色只允许必要的原 Mission/状态读取及
POST /v1/verifier-evidence，不能接纳/取消动作或管理 registry。原 grants 和历史保留。

## 原输入和时效

生产者读取原已接纳 wire plan，校验其字节摘要及 Controller 规范化 plan digest；
校验租户、admission、Task AwaitingSatisfaction、全部当前角色已经本地 Completed、
原 node/entity Actor 绑定、source revision、contract、exact predicate 和五秒上限。
人工确认必须再次引用 inspect 给出的完整上下文，observer_id 是本地操作者声明的
现场报告归属，不是额外远程身份认证，也不形成硬件身份认证。

source_observed_at_ms 表示发布器本次收到人工报告的时刻，不是定位传感器的测量时间。
五秒总预算覆盖获取当前绑定、持久化及发布，按本机 monotonic 扣除，禁止重复提交续鲜。
原人工输入、原 evidence、sequence 先持久化 publishing，再发送唯一一次请求。
丢响应、崩溃、超时保留投递不确定；重启或重复输入不得再次发送或更改原时间。
有原回执才返回原回执；同 observation_id 异输入拒绝。未知现场结果保持 unknown，
不能伪造正证据、自动重试动作或释放待验资源。

## 能力目录作用域

原目录把所有导航都要求同一个巡检 verifier，导致 0.5 米草稿在 Reviewer 被拒。
修订为：通用本机委派按各 Task 的明确 satisfaction；固定命名目标巡检要求本到达源。
execution-report 只证明本地委派生命周期，不能证明实际移动/到达。
这不解锁有界 0.5 米动作；原生距离、时长、停止合同仍未证实，草稿还遗漏现场条件。
不改写原失败 Request、历史 execution-report 或现有 Task 的验收依据。

## 验收与边界

独立 loopback Controller/两个软件 Node 验证：未登记不能接纳目标合同；正式登记及
原回执重放；本地 Completed 后资源与后继仍被等待；错计划/attempt/租户/主体不能
满足；unknown 不释放；正确受限人工源只推进自己的后继；重复/重启不重复执行。
这些是无运动软件证据，现场 registry GET、独立权限和恢复另列实际结果。

只对上述两个固定地标有效；地标定义变化必须重新审核源修订与新计划，不借同名地图
跨狗复用。到达证据不证明共享门口已清空、原狗已停止、恢复授权或 Actor 可更换。
自动到达验证另需原测量时间/质量、当前实体映射、目标几何关系及停稳观察窗口，
不能由本次人工 source 的登记或普通 Mapping HTTP 响应推导。

## 本次结果

193 项集成测试、739 项 MI 回归通过。R7 实际 Controller/双软件 Node、独立 mTLS
的九组检查通过，四次唯一软件 Execute、120 条原事件、最终占用为空。私有夹具首轮
因遗漏 Artifact v3 原 owner 导致公开事件读取 503；原失败保留，显式配置同代际后通过。
首次现场登记又因工具给全局管理员携带租户头而收到 403；修复实际请求头并补测试后，
使用原目录完成登记及原命令回放，不改核心权限规则。详见现场验收记录。

ThinkPad 已登记 onsite-patrol-arrival revision1 与受限 rg-patrol-verifier/local 证书。
登记、原回执和原 Mission 历史通过实际 Controller 冷重启。没有创建新 MI 模型 Request、
接纳新 Mission、发布到达正证据或执行机器人动作。服务 ready 不代表所有物理数据新鲜；
现场状态/地图报告的最新可用性在验收记录单独列出，不能把只读 published-contracts
校验无阻塞解释为当前可运动。安装工具仍要求 role-map 独占更新，残留安装锁须人工
核对后恢复，不能宣称无人值守安装恢复或跨部署工具的文件 CAS。
