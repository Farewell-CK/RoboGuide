# ADR-0120：原目标下的运行期计划候选与安全切换

日期：2026-10-20。状态：Accepted，实施中；尚无整体软件完成声明。

运行期计划修订由 Orchestration 管理版本，Control 继续独占 Group、资源和 ActorBinding，
Runtime 保留原 attempt/command，MI 只提交候选。不能覆盖原计划、删除原始执行证据，或
用新 Task ID 隐藏已完成操作的重复执行。候选、Review、切换意图和最终结果均先持久化。

只保留 goal 文本不足以证明新 DAG 仍实现原目标。安装修订策略必须冻结独立的 Mission
goal verifier、原 goal/context digest 和实际 Control source；该 source 的 producer 不能是
策略安装者或候选生成者。Orchestration 安装原目标完成 gate：修订后全部 Task 完成仍
不足以完成 Mission，必须有当前版本/Group/完整 attempts 的新鲜独立 goal evidence。
旧版本、未知、过期、权限撤销或缺原证据保持 pending/incident，不能用模型结论补成成功。

原始 Mission goal、Actor 定义/grounded identity 与物理 binding 语义固定。已语义满足的
Task 在目标计划中必须逐字段保留；其已完成 Control 事实从原真实 Group 继承，不 Execute。
其他目标 Task 使用此前历史从未使用的新 Task ID。旧未完成 Task 与其 attempt 永久留在
原 Group/版本历史；不能把 Cancelled 或 local Completed 改写成语义已满足。
显然重复已完成 intent/effect 的候选默认拒绝，不能仅凭模型改名取得重做许可。

部署/管理员为实际 Mission、tenant、稳定 principal 和当前计划版本安装有界修订策略。
原期限、候选/版本/切换次数不因重启、失败或新命令 ID 刷新。自动切换只接受原策略明确
预审的完整候选 digest；新生成候选需要实际有权 reviewer 对 exact digest 提交 Review。
候选接受仍经过完整 semantic/deployment/mechanism/verifier 校验，模型没有节点或资源权。

切换意图冻结 source plan/group、target digest、新 Group、全部原 attempts 与 policy/Review。
先持久 Mission hold，再对完整原 Group 请求 typed stop 和 Memory revoke。没有 fresh
independent stop、原 provider revoke、当前 owner/session 与全部原 deadline，就不释放
资源、不提交新 Group、不 Execute。超时/Unknown/授权变化进入保留所有权的 manual hold。
接受取消不等于停止；SoftwareProcess proof 不等于设备停止。

全部门禁齐备后的单一应用事务将原 Group 标记 Superseded 并释放其承诺，保留旧 Task/
attempt/history；Control 创建新 Group，只从真实原 Completed Task 继承完成事实。
新 Task 重新 Match → Propose → Commit → Bind，Memory 再准备/验证/许可后才 Execute。
计划的 accepted_at/整体 timing 原锚点不更新，原完成证据/预算/历史不可被新版本改写。
后续旧 attempt/旧 verifier 输入只按原版本归档或拒绝，不推动目标计划。

原始提交 receipt 保持不变，Mission 当前视图同时暴露 current revision/group 与版本链。
restore 必须校验版本链、所有原 Group、完成事实、候选/Review/策略身份和原预算。
新增权限、终态与零事件修订状态由 shared wrapper v43 / inner v23 / codec v28 fence 保护；旧数据只可显式迁移，
旧 writers 不可忽略新增修订权威。实际软件文件任务/SQL/TLS/独立 verifier 验收不生成设备。
