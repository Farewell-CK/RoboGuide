# ADR-0104：State 消费者、Belief 与通用语义验证

状态：Accepted，2026-10-15。对应完整非发布整改计划 F3；不声明设备验证或整体整改完成。

## 决策

State 继续是保留独立来源的证据联邦。部署通过有 revision、command ID、原始证书主体与
内容 digest 的 registry 明确规定：哪个 tenant、来源、object/channel/schema、TTL、confidence、
冲突规则及 typed condition 可以影响哪个消费者。配置更新和原始 receipt 在同一 checkpoint /
authority journal 事务提交；失败不先发布。历史完整保留，不能同 revision 换含义或删除旧策略。
Controller wrapper 升至 v30，新增事件使用 codec v21，旧 wrapper 不能携带新 authority。

Control Matching、当前 State Commit、初始 Bind、Rebind、续执行准入及 Execute outbox
读取原始证据。初始 Bind 核对 Commit 时的 policy digest，并重新检查当前来源；无 State 的
兼容接口不能绕过已安装策略。Readiness 在持久 intent 实际发送前再次读取时钟和当前 State。
Satisfaction 门禁通过后才允许 Orchestration 释放任务资源；本地执行已经完成的事实独立保留，
等待门禁不会重新 Execute。原始选中 record 和解释 revision 写入决策事件。

Belief 由单独的显式 producer 产生，采用 `belief/{tenant}/{policy}` 身份。输入只接受声明的
原始 Node 语义，不允许递归消费 Belief。布尔/枚举按等价或最高置信度规则归约，平分冲突
保持 unknown；最低独立来源数由策略配置。数值融合只处理共同单位、共同 frame 中的线性
Gaussian 向量，必须提供对称正定协方差，采用有界确定性的 covariance intersection。
confidence 只控制来源准入，不能变成方差。维度、来源数、权重搜索和分歧阈值都有显式预算；
缺协方差、退化、单位/frame 不同、来源不足或冲突不产生肯定值。

CI 使用信息矩阵的凸组合并在配置网格上最小化 covariance trace；不估计未知的交叉相关。
输入 covariance 本身必须是有效不确定度上界；本算法不能证明传感器校准、非线性姿态、
物理定位或真实世界准确性。方法依据见 [KIT 原始研究](https://publikationen.bibliothek.kit.edu/1000035130/2619395)。

原始 State 事件保持完整。Belief 保留 input key/digest/epoch/sequence/receive time 和策略 digest；
其有效期不超过任一输入的原始 TTL/age fence。相同 derivation 不续期。Node 的当前 session、
身份及 producer revision 改变时，旧 Belief 不能继续授权。重启不把旧 session 变成当前会话。

通用 verifier 是独立于本地完成的受授权来源。接纳计划时冻结每个 Task 的唯一来源合同；
报告绑定 tenant、normalized plan digest、完整当前 Role/attempt 集合、predicate、source
revision、finality、sequence 和非续期 receive-relative validity。可要求真实 CAS 字节及精确
长度/digest。原始报告先持久化再消费；Unknown/provisional 不满足任务。正面最终证据只在
AwaitingSatisfaction 后推进 DAG，负面最终证据进入 SatisfactionRejected，不能改成本地失败。

持续来源在 Execute 前安装完整 attempt 集合的持久初始期限。未取得肯定观察、过期、来源
撤销/更换或负面观察触发整个受影响 Group 的既有停止/hold 流程；deadline 由来源策略声明。
receipt 仍不证明已停止，资源继续保留，物理停止依赖独立 operation-owned safe-state 证据。
已完成 Task/Mission 后的争议保留 incident，不回写终态或重新执行。

Mission SQL 同事务保存 Request 与模型/配置 provenance。记录实际 Provider 返回的 model /
response ID（缺失为 unavailable）、请求/响应 digest、原始时间与异常类型，保留独立 call ID。
配置、Prompt、Catalog、审批策略、代码和运行时依赖 identity 随原始 deliberation 固定；审批、
重新提交及部署 reconsideration 不得在来源变化后复用原 draft。历史缺失保持 partial/legacy
unavailable。查询分页有界，不触发模型或 Controller mutation。Provider 返回字段不是后台模型
真实身份的密码学证明。

## 权威与兼容

Node Protocol 不承载 Memory/CAS payload，也不接收模型指定 Node 或资源。State API 是按
可信 source/tenant 筛选的 readonly federation；原有 loopback profile 可显式使用 legacy
本地 source，不能把它升级成硬件身份。v0.1–v0.3 initial assessment 仍可读，新 State policy
exclusion 必须使用 v0.4。旧 Request 可查询、取消或只读 reconciliation；缺原始 deliberation
identity 的 draft 不能直接获得新审批或重新提交，需要新对话及完整审查。

本阶段正向进程验收使用实际 POSIX 文件计算、正式 RoboGuide Node、CAS 和独立复算进程。
它证明相应软件链路，不证明机器人、硬实时、真实物理停止或设备连续性；这些保持
HardwareEvidencePending。Memory/Spatial、HA、quota、耦合恢复、运行中抢占/计划和 Federation
继续按 F4–F10 实施。
