# ADR-0249：已登记验证合同的 MI 部署声明与原计划导出

状态：实施与无运动验证，模型/现场结果分别记录。日期：2026-11-11。

## 问题

Controller 已登记到达源，但 MI 的 World/Memory grounding reader 不读取管理员 verifier
registry；原能力目录只说使用 exact predicate，未携带原精确字符串。真实双狗规划因此
产生 REQUIRED_BUT_NOT_SUPPLIED 占位符，Reviewer 拒绝。手工 patrol.py 模板通过
Controller 只读校验不代表真实 MI 已能产生正确计划。

人工到达 publisher 另要求原 accepted wire plan：MI Controller 使用模型 codec 后的
compact UTF-8 编码，漂亮排版或字段顺序改变不能当成原提交字节。

## 决定

复用已受信的部署能力目录向 Planner/Reviewer 提供支持声明，不新增服务或重命名为
World。纯转换器 verifier_catalog.build_arrival_catalog 先核对原完整 registry view、
配置源的 revision/tenant/principal/contract/精确predicates/5000ms及六项目标引用摘要，
拒绝歧义源、错误字段与已追加快照的旧目录。只在既有 arrival capability 的 description
追加明确的 cached deployment snapshot，不改其他能力、操作或参数。只暴露选中来源，
其他租户内容不进入模型输入；完整原 registry 摘要仍保存。

调用者负责实际 mTLS 管理员读取、原文保留和配置发布。转换器不认证 transport，
不登记来源或产生运动权限。快照包含原 registry/config/目标引用与摘要；输入输出目录
摘要和实际 MI CanonicalCapabilityCatalog 规范化摘要分别保留，不假定两者字节相同。
MI 既有 provenance identity 中的 catalog_digest 冻结这项部署声明；不修改旧 Request。
这是显式部署更新，不宣称 MI 自动实时发现 registry，也不是测量、定位或到达事实。
Controller 原准入仍负责当前来源授权。执行前重新检查原来源修订及当前 Scene/状态。

local-task-v1 的实际两狗操作分别要求本机 pilot-task-slot；部署目录明确每个 Role 的
Space units1、task resource scope。它描述本机执行槽，不虚构共享门口或改变所有平台
的通用 task.delegate 语义。旧失败草稿漏资源的事实保留。

arrival_plan.encode_plan_for_arrival 使用真实 MissionPlan codec 和 Controller 编码，
严格核对原 admission wire SHA 和 Mission identity，保留同 Mission 的受信 tenant
投影所携带 Core opaque source_plan_digest；Core 哈希内部类型，不能重算为 MI JSON 摘要。
显式非零 revision 拒绝；缺 revision 不证明当前性，publisher 继续读取当前 revision、
AwaitingSatisfaction、原完整 attempts。工具不发送 HTTP、Task 或证据；save_plan 仅以
0600 创建新文件并 fsync，不覆盖原证据。无法重建原字节时拒绝，不能漂亮排版后续用。

## 验收边界

220项 Linux 集成测试通过，包含实际 MI Catalog 序列化和原计划 codec；原 MI 实现
字节未改。真实 MI Request 分别记录模型生成、Review和审批前状态，不用假 Planner
替代模型通过声明。所有部署可执行 operation 均保持无条件人工审批；本轮不调用批准。
底盘断电时 Scene/定位/readiness 未知必须保留，不以目录支持声明补当前物理事实。
原两狗 Controller attempts、资源和原失败 Request 始终保留。实际导航另按当轮授权。


v0.2快照显式给出每路supported_task_satisfactions，expected_effect必须与该路predicate
相同，不能承诺同时共存等更强共同目标。统一navigation_preflight只组合原报告地图、
位置/姿态及已登记目标引用，并扣同Controller留存和本地读取预算；它是该实验的纯
准备入口，不新增Core运动/定位权威或守护进程。模拟正常/故障分别验收，真实审批前
用认证原输入重新检查；没有自动接入所有MI审批入口的承诺。
