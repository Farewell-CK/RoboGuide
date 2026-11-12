# 2026-11-12 狗1站立与昨日取消结案

本轮狗1原地站立真机验证通过。真实链路为MI→RG→狗1Robonix，原生记录有一次
stand调用及成功leaf_result；RG后续新鲜姿态standing，现场操作者明确确认已站起来
并站稳。没有导航、前进、返回、趴下或自动重试；狗2不参加本轮动作。

## 授权与初始阻塞

用户明确允许预检通过后狗1一次原地站立，确认底盘上电、平地、起身空间、无人接触、
遥控器连接、现场观察及接管条件。狗1初始Pilot READY，Node/Connector运行，姿态
down、电量88%。本机模型仍gpt-6-luna，原RTDL/planner源码与昨天一致；没有证据表明
昨天的规划问题已修复，也没有通过重试动作测试猜想。

初始中央预检发现昨日Mission `mission-47b837b9b40f4cf2a2a30a4a3c63a238`安全隔离，
狗1Failed、狗2Unknown，两槽均bound。现有取消/修订路径没有单Failed-Task释放接口；
新Mission不能改资源名或省略资源绕过。单Task失败隔离策略仍是另一个未实现能力。

用户补充狗2Thor供电、底盘断电，并提供新IP192.168.2.34。以原Tailscale身份
100.111.156.77的SSH主机密钥核验连接，没有盲信新地址。最初Tailscale离线和旧LAN
地址无法登录的证据保留；恢复后仅处理原取消，不发新狗2任务。

## 通过原合同结案昨日取消

遵循[ADR-0233](../../decisions/0233-local-administrator-cancel-settlement.md)及
[ADR-0232](../../decisions/0232-task-cancellation-settlement.md)，没有新增释放接口：

1. 只读核对原dog2 handle `1f7d7d34-3b59-4262-8f43-99ec95e66409`、原session/turn、
   request digest、sequence18及8条事件。原任务早于本次boot；原流interrupted，
   只有读取能力说明的前置计划。当前底盘断电来自现场事实，不伪装传感器测量。
2. 独立审阅精确管理员命令后，通过RG将旧Mission置Cancelling。原stop record、
   原期限、代次不变。没有新Execute。
3. 停止原dog2 Connector取得唯一journal写权，SQLite backup留在dog2主机；核对实际
   部署manual_settlement/journal模块SHA与已验收源相同，实际CLI再次检查原audit摘要。
   管理员以stopped结案，明确不是no-effects或原生终态；只改变一条run，原events
   逐值不变。原Connector恢复后由实际Node报告Cancelled。
4. Controller认证管理员针对原record digest、generation和原本机回执独立reconcile。
   回执physical_stop_confirmed=false、automatic_continuation_authorized=false。
   正常取消收尾将旧Mission置Cancelled、两槽释放、Unknown归零。

原stop record SHA保持
`sha256:1ae4ff6561713c2017270867ba2208d970870e9f4903a955975361c2b1c07813`。
本机audit使用实际journal.canonical（ensure_ascii=True），为
`c48eb90a2c98522d5d7f379e8ad9bed295937794e032b2cece0e9906f758346a`。
备份SHA为`3759d777b26cdfba57a498edac048f789e2d33bd3218c31f8f570d62755b8bf9`。
首次只读准备采用另一JSON编码所得摘要被明确作废；没有用它执行结案。
备份及原请求/事件不导出到公开包，保留在源主机。

## 本轮实际MI与RG执行

| 项目 | 实际值 |
| --- | --- |
| MI Request | request-55589534e86f4318bd0b75375d2654e8 |
| Mission | mission-2176e7df530f4b42aaec50b269ab1d13 |
| 原草稿revision | 1 |
| MI digest | sha256:4fc58d7ba4358bf91eb4029a958aac7bd8b1b4a9021a6af745c2903f4572ab95 |
| Actor | actor-1 → entity-lite3-dog1 → lite3-dog1 |
| Context | stand-context，independent，单执行者，无关系/跨狗协调 |
| Task / Role | stand-once / delegate-role |
| Contract | task.delegate@v1，唯一参数profile=local-task-v1 |
| 资源 | dog1-pilot-task-slot，Space1，Taskscope |
| satisfaction | execution-report，仅委派生命周期及报告 |
| admission wire SHA | sha256:693fd6e0886a1885f9ad5ff3b6a433d871c1c692634f47ba81092b4322db8afb |

实际MI生成及Reviewer通过，另有独立只读复核。草稿在AwaitingApproval停留，旧占用
释放前没有批准。释放后重新读取原物理路由、能力/资源、RG新鲜姿态、电量及本机
Pilot READY；按本轮明确授权批准原revision/digest，没有改原草稿或重新生成计划。

本机objective完整保存现场事实，先查姿态/电量，已standing不重复起身；down且本机
控制权/安全允许才至多站立一次，随后如实报告；禁止导航/前进/趴下/自动重试。
原地站立How与本机安全由Robonix负责，RG没有直接控制底盘。

09:47:11.134（北京时间）MI审批接纳，RG正常匹配、Commit、Bind，唯一attempt为
`attempt-46:group-mission-2176e7df530f4b42aaec50b269ab1d13-10:stand-once-13:delegate-role-1`。
约61秒后原生任务done；09:48:15中央观察Mission/Task/attempt均Completed，资源为空、
Unknown=0。原admission wire字节通过正式codec重新导出并核对，未重发请求。

## 原生调用、RG报告与现场事实分别核对

唯一native session为`3fe34867-8b08-4f65-b00a-c4997765869c`。原适配器SUCCEEDED，
dispatched=1、cancel_requested=0，24条原事件：

- Plan41db-1读取底盘状态；Plan41db-2再次读取状态并包含唯一
  `robonix/primitive/chassis/stand`，call ID `41db-2:1`。
- stand具有实际node_state.leaf_result：success=true、error为空；随后同call进入
  batch_result汇总，不能重复计为两次起身。
- seq23 TaskState T1 done，seq24 final_text。判定没有仅依赖模型最终文字。
- 后续只读缓存standing、电量77%、底盘报告age约192ms且未过期，Pilot重新READY。
  狗1Node/Connector本轮未重启，未改模型、Robonix程序或配置。
- RG单独读取新鲜aggregate posture=standing；现场操作者回复“已站起来并站稳”。

本轮未登记或冒用姿态verifier；没有向导航到达源发布站立证据。execution-report
本身不等于独立物理站稳验收，当前结论由上述三类证据联合记录。

## 独立软件诊断及剩余限制

在ThinkPad独立目录提取原SSE行解析函数，7项离线回放测试通过，覆盖9个适用夹具
及UTF-8/工具片段累积。没有模型、Pilot或设备连接；范围不含完整HTTP/EOF、planner、
RTDL或现场二进制。提取块和脱敏导出源SHA均保留。

源码只提取delta.content字符串，reasoning与正文分离，若干不支持的有效JSON形状
和无choices的error信封会被忽略。它解释了空正文如何可能出现，仍不证明昨天实际
上游返回何种形状。当天一次成功也不证明历史规划故障已永久解决。

昨日提交99da6d5a的显式Unknown Node修复未部署现场。本轮未改RG代码/部署，只有
正式取消管理操作和新授权动作；Controller/MI始终使用原PID9841/15045。
共享门口协调、双狗分区巡检、故障接替及持续编队均没有在本轮验收。

## 原始证据

Windows：`C:/workspace/DEAIOS/lab/rg-dog1-motion-20261112`。
ThinkPad：`~/rg-lab/dog1-motion-20261112`；狗1/狗2各自保留同名原始现场记录。
MI原命令/审批、预检、执行、取消与管理员回执、原native事件安全投影、人工现场事实
及离线测试均保留；主机凭据、私有journal备份和Prompt/响应正文不进入公开证据包。
