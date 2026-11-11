# 2026-11-11 独立现场到达源登记验收

本轮授权范围：准备、接入和无运动软件验证。没有创建新的 MI 模型 Request、接纳巡检
Mission、发布真实到达正证据或下发机器人动作。原站立、0.5m 失败草稿、照片接力和
全部历史保留。代码在 `codex/patrol-verifier-admission`，基于 `f686ce22`。

## 已完成的合同

一次性人工现场到达发布器通过原通用 semantic-verifier 入口工作，无新常驻进程。
独立 mTLS principal `rg-patrol-verifier`，角色 verifier，租户 local；与管理员和两狗
Node 证书不同。原 operator grants 保留，部署授权从 v0.1/revision2 升级为 v0.3/revision3。
源 `onsite-patrol-arrival` revision1，enabled=true、continuous=false、max_age_ms=5000。

只绑定以下原目标，不适用于一般相对移动：

|实体/执行节点|目标|原 Scene 区域|地图标签|
|---|---|---|---|
|entity-lite3-dog1 / lite3-dog1|茶水间门口|anno.2cb4c8ba|1101_visual_dog2|
|entity-lite3-dog2 / lite3-dog2|王老师办公室门口|anno.4c5ac08a|1101_visual_dog2|

两狗各自原目标来源摘要与早前 v0.2 验收相同，登记前从 RG 原 World 重取，source gap
为零；同名地图不表示共享 frame。exact predicate 绑定实体、Node、目标、地图、区域
和该狗原目标文档摘要。原 accepted wire plan、Controller plan digest、租户、固定 Actor、
全部当前 Completed attempts 与 AwaitingSatisfaction 必须一致；当前 v0.1 只支持
plan revision0 和精确5000ms合同。正证据仍需现场人员逐任务明确确认。

source time 是工具收到人工报告的时刻，不是机器人定位测量时刻；observer_id 是本地
操作者声明的归属。五秒 monotonic 总预算覆盖绑定读取、落盘和发布，原 input/evidence/
sequence 在 POST 前持久化；丢回复保留 uncertain，重启不自动重发/续鲜。Unknown
仅保存本地记录，不发正证据、不推进后继或解除占用。这不授予共享门口清空、物理停止、
恢复授权或换 Actor 接替权限，也不形成自动定位或自动停稳算法。

## 软件与真实进程验证

- 最终冻结 source-r3 的193项集成测试通过；739项 MI 回归在 r1 通过，r3全部 MI代码
  字节未变。Ruff、格式、严格mypy、函数文档和diff检查通过。
- 独立 R7 Controller、双软件 Node、受限 mTLS，CPU1/1GiB：九组检查通过。
  四个唯一软件 Execute，120条原公开事件，四次 PlanCommitted/ExecutionGroupBound/
  MissionActorBound/TaskExecutionBindingsReleased，最终 ExecutionGroupReleased、占用为空。
- 未登记时拒绝合同；登记原命令幂等、异输入拒绝；本地 Completed 仍等待证据、保留
  资源且不启动后继；错计划/attempt/租户/主体拒绝；Unknown 保留占用；两次正确的
  软件人工报告只分别推进自己的软件照片后继。冷重启 Controller/双 Node 与 publisher
  不增加 Execute，原命令回执、原事件前缀和最终释放均保留。
- r1夹具遗漏 Artifact storage v3 原 owner，公开事件查询真实返回503。保留原失败；
  r2只显式配置私有CAS同代际，公开事件按原路通过，没有绕过检查或改核心。
- 首次现场登记工具给全局管理员附带 local 租户头，GET registry 收到403，尚未修改
  授权或创建源命令。修复请求头并补实际 HTTPS headers 测试，原目录恢复成功。

## 正式登记证据

原命令 `enroll-onsite-arrival-c5aee3b1a2af412797b7dd7b03ad0b78`，expected_revision0；
接受 registry revision1，原回执 accepted_at_ms=1794387214751。
精确重复及实际 Controller 冷重启后的第三次重复都返回原回执。
受限证书 GET inventory=200、GET registry=403、GET Mission列表=403；动作/取消/管理
写权限另在隔离进程验收拒绝，现场没有发送这些写请求。

同一只读巡检计划 body SHA256 `92236d3d0c5dfba61251dab236285f9b3fea3b96f7b2bc156491fa55ee689c56`：
登记前 `verifier.source_unavailable`，登记后 published-contracts 校验 `not_blocked`、无issues。
这是合同只读校验，没有提交、Commit/Bind 或获得本轮运动权限，不表示实时执行预检通过。
Controller 冷重启后 registry 完整一致。最初脚本错误地把两次 `/v1/missions` 的404
当成历史一致，不能证明任何 Mission 历史。该原记录保留并纠正：追加一次受控冷重启，
先从成功的 execution-attempt-history 取得52个有 attempt 的 Mission、55个attempt，
逐个核对 Mission、tenant、admission、完整attempt投影及全局verifier来源/证据，
重启前后正文摘要全部相同。此检查覆盖上述历史投影，不宣称校验了所有 Artifact 原字节。

## 部署问题与当前边界

ThinkPad DHCP 从192.168.2.20变为192.168.2.29，Controller 绑定旧地址反复启动失败。
保留原配置后，只调整 RG listener、证书和两狗 RG 端点：listener 使用0.0.0.0，客户端
使用稳定 Tailscale100.116.9.127；保持原CA、Node身份、数据库、Artifact、Pilot journal。
只重启原 RG 服务，不操作 Robonix/底盘。两狗调整前后原 journal 行数与全表摘要相同：
狗1 runs1/events6，狗2 runs37/events674，没有新增原生任务。

同时核查到旧 MI drop-in 实际仍指向前一日 source-r2；冻结的 source-r4 目录存在不能
证明当时运行了它。本轮明确切至验收过的 source-r3，核对 /proc/PID/cwd，保留原MI
数据库和审批规则。目录合并仅改三个 description，五个capabilities、四个operations、
全部属性和参数保持；Artifact端点同步Tailscale。Controller和MI实际ready。

最终 completion-state 读取两Node Reachable，但两狗 task.delegate 均未出现在可用合同中；
不能从主机在线推导本机动作就绪。此前 task.delegate 可用性和 Scene current 出现波动；最后
MI采集有四条State、两条Memory，两条Scene目标因not_current被拒。先前一次还拒绝
了过期/不合格姿态，保留这些真实缺口，不把服务ready解释成物理数据或运动就绪。
本轮没有进行新模型巡检规划，也没有证明 MI 已自动发现登记源；登记后的具体真实
任务仍需当前来源、完整现场条件、精确验收合同和另行授权，不能跳过这些步骤。

0.5m 草稿仍不能执行：本机有界距离/时长/停止合同未证实，原本机目标还遗漏部分
现场条件。巡检目录规则已缩至固定命名目标，登记本源不为该相对移动补造能力。
完整双狗拍照汇总、门口资源协调、停止/恢复接替和自动到达均未在本轮验收。

登记工具对部署 role-map 要求独占更新，本地output锁不是跨工具CAS。kill/断电后
遗留安装锁需确认无存活安装进程再人工移除并恢复原目录；没有无人值守安装恢复承诺。

原证据在 Windows `C:/workspace/DEAIOS/lab/rg-arrival-verifier-20261111` 和ThinkPad
`~/rg-lab/arrival-verifier-20261111`；公开36项过程JSON与原失败保留，私钥仅在现场。
原 public JSON 文本导出有编码替换风险，另逐字节Base64导出
`field-enrollment-exact`，与现场原文件SHA256一致，作为命令/回执权威副本。
相对文件及SHA256见[证据索引](2026-11-11-arrival-verifier.evidence.json)。部署回退保留
当前新授权及全部新数据，不能回退旧授权revision或覆盖旧数据库；优先前向修复配置。
