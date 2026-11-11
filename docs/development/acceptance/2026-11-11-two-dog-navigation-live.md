# 2026-11-11 双狗分区导航：首次真实下发与失败证据

本轮真实导航未通过。软件接纳、固定实体分配、Commit/Bind 和两路本机 Pilot
委派已发生；没有到达正证据，也没有完成或释放占用。现场反馈“两只狗都在爬着”，
暂按仍趴着理解，原话保留；不将此解释升级为正式停止、恢复授权或到达证据。

## 授权、预检和原计划

用户明确授权：预检通过后，狗1到茶水间门口，狗2到王老师办公室门口，各一次，
到达停稳。已确认底盘供电、遥控器连接、路线平整畅通和现场观察接管。没有授权
自动重试、返回、趴下、故障注入或本轮失败后的新动作。

上电后的首次读数尚未就绪；等待原系统启动后，两狗 Pilot、报告姿态/位置、Scene
和中央能力恢复，未重启或修复本机服务。新鲜中央预检通过，Unknown=0、占用为空、
原55个 attempts不变。报告位置不等于定位质量已验证；Robonix保留本机How及安全权。

实际模型 MI 新Request：`request-4a564a3422674279afd9c90947284e37`。
Mission：`mission-47b837b9b40f4cf2a2a30a4a3c63a238`。
MI Review及独立复核均通过；仅批准新草稿revision1、digest
`sha256:a44e8045e3de6d070e74c9eb270259dc3afdafe25a77949ecfe8ae26c7d5bcf6`。
原软件预演草稿未批准或替换。

同一Mission、两个固定Physical Actor、一个independent Context、两条无依赖Task，
distinct-physical-entities；各Role占本机一个Space槽，resource_scope=task。
每路expected_effect与已注册原谓词逐字相同，由`onsite-patrol-arrival`独立确认。
导航本机必要前置由Robonix承担，没有另加姿态Task或直接控制底盘。

| Task | Actor / Node | 本机目标 | 原 Scene 标注 |
| --- | --- | --- | --- |
| dog1-tea-arrival | entity-lite3-dog1 / lite3-dog1 | 茶水间门口 | anno.2cb4c8ba |
| dog2-wang-arrival | entity-lite3-dog2 / lite3-dog2 | 王老师办公室门口 | anno.4c5ac08a |

各自地图标签均为`1101_visual_dog2`，未将标签相同解释为共同坐标系。
目标引用摘要、原Scene字节摘要、现场条件及禁止项完整保留在原MI计划/本机objective。
原admission wire SHA为
`sha256:e51cda259d0207e71ca069a070be106be6fd18d0ad7e7f57705d9dfdef2da7aa`，
由正式MI codec导出并核对成功；Core内部source_plan_digest单独保留，不与MI JSON重算混用。

## 真实执行时间线（北京时间）

| 时间 | 原始事实 |
| --- | --- |
| 18:11:23.329 | MI审批后Controller接纳；两路各完成匹配、Commit、Bind，随后各一次Execute |
| 18:11:58.505 | 狗1报告TaskFailed：native RPC `truncated JSON response`，basis `robonix-preplan-failure-and-idle/v1` |
| 18:11:58.514 | Controller将整个Mission安全隔离，向仍活动的狗2发取消 |
| 18:11:58.969附近 | 狗2原Pilot收到abort、随后流中收到既有前置计划和`interrupted` |
| 18:12:02.838 | Node连续五次无法映射显式UNKNOWN，报告ExecutionRecoveryRequired |
| 后续复采 | 狗1Failed，狗2Unknown；两项Task仍Active、Mission隔离，两个原槽保持bound |

Controller原始事件序号：2489808–2489826涵盖接纳、选择、Commit、Bind和激活；
2490362为狗1原始失败；2490363为安全隔离；2490432为狗2恢复要求。
已保留认证HTTP投影及限定窗口内原始SQL事件，没有清空、重写或全历史重放。

狗1：唯一native session `b0eb4436-7b0b-45f5-bc65-78558c4e7c7d`，
原task turn `89b1df22-e3ae-4717-bb96-4ebe09f0b2eb`。无Plan/CapabilityCall，
`completed_plan_ids=[]`；原abort答复无活动turn/plan。原Pilot日志18:11:34.544
出现`[pilot/rtdl] output correction 1/2: truncated JSON response`；中间纠错报
`operator children must be an array`，首次和最终解析前的raw_content均为空字符串。
只读核对本机`rtdl_stream.rs:402`及`planner.rs:1681–1685`，最终错误来自RTDL
模型流结束后`parser.finish()`发现内容不完整；gRPC随后转发Internal错误。
当前manifest模型为`gpt-6-luna`，未显式设置max_tokens/max_output_tokens/timeout。
仍无法判断空内容由模型、上游流还是客户端抽取造成，不能认定token上限截断。
失败后缓存down、Pilot READY；没有改Robonix配置或额外发模型请求。
源码片段仅用于解释错误路径，尚未核验其与运行二进制的版本对应。底盘断电后，
Thor1固定SSH地址连续两次超时，未继续探测或用新模型请求验证猜测。

狗2：唯一native session `cd97de93-0283-442a-8287-6cc827128ed2`，
原task turn `ab059f70-9ac0-44e6-99c0-778878ddae1f`。仅8个事件；唯一plan
`2fc2-1`为导航前置检查，唯一计划CapabilityCall是executor的
`robonix/system/executor/builtin/read_capability_doc`，未见该调用执行结果，
也未观察到站立/导航调用。计划接收晚于abort约5.5ms，但abort前task_state已引用
此plan，不能据此宣称“停止后重新执行”。约194秒后事件仍未增长。

狗2适配器保持UNKNOWN，原detail为`Pilot stream ended without an attributable
terminal task result`，cancel_requested/abort_sent均为1；任务槽未释放。
最新缓存down、电量86%，Pilot因原任务占槽而UNAVAILABLE。缓存姿态、abort回执、
无新增事件都不替代正式停止合同；停止记录为manual_escalation、HardwareEvidencePending。
本轮未发布arrival/verifier正证据，未恢复/取消旧占用，未新建重试attempt。
随后操作者明确报告底盘已断电，并要求继续分析；原话另存onsite-poweroff.json。
不再进行任何运动或模型动作请求，也不将现场断电报告自动变成已发布的停止/恢复证明。

## 已定位的边界和缺口

1. 两路固定实体分配正确，没有串狗。本轮最早失败发生在狗1Robonix规划阶段、
   产生可执行Plan之前，
   不是已观察到的起身或导航失败。需要Robonix原错误/模型响应诊断，不能先假定
   加一个独立站立动作即可修好；本轮不修改其模型或控制服务。
2. 单Task失败触发全Mission停止，是当前fail-fast策略。`independent`只描述
   执行协作机制，不承诺故障隔离。`application/outcomes.rs`及`safety_stop.rs`
   当前收集全组活动attempt；不能临时跳过停止门禁来让另一狗继续。
   若要独立任务继续，应另立版本化失败策略和Mission结果聚合合同。
3. 显式UNKNOWN未进入Node映射合同，是接入缺陷：原模板与公共类型均缺Unknown，
   因而原始未知被连续解析错误掩盖。修复须显式持久化Unknown/待对账，保留原detail、
   attempt及锁，不得伪装Failed/Cancelled或重Execute。软件修复记录与现场版本分开。

## 显式Unknown的软件修复（未部署现场）

[ADR-0250](../../decisions/0250-explicit-local-execution-unknown.md)增加可选unknown映射，
直接保留原detail、ReconciliationRequired/Unknown及原锁，继续只读查询同一handle。
未声明值仍拒绝；空映射保留原编译工作流摘要，启用UNKNOWN后摘要明确变化，不能
将新配置直接冒用到旧活动attempt。Mission fail-fast和物理停止合同未修改。

新增8项回归测试，覆盖首次Unknown、保锁/防重复Execute、原handle后续终态、SQL重开
恢复和配置/摘要兼容。静态独立审阅通过；ThinkPad独立冻结源码source-r1，专用构建
目录，保持现场Controller/MI原PID。没有更新两狗Node或Connector。

首次全仓`cargo fmt --all -- --check`未通过：差异只在未改动的
`core/state/src/event_log.rs`及`core/state/src/lib.rs`。原日志和非零回执保留，不将其
写成全仓检查通过，也不为本轮混入排版修改。随后在相同冻结源码上分别检查本次
Node包格式、全部Node单测及Node全部target/feature的Clippy，结果在软件证据中单列。

首轮Node测试147通过、3失败：新增摘要夹具缺invocation.parameters，两个新增显式
Unknown测试误用旧“暂时I/O失联不得生成Unknown”的等待helper。仅修正两个测试文件，
旧helper断言及生产逻辑未改。source-r1及失败原日志保留。source-r2首次重跑又暴露
测试脚本的缓存问题：确定性tar的mtime=0与共用缓存导致Cargo复用旧测试程序；这次
回执不能当作新源码的有效测试结论。旧测试程序及SHA已单独冻结，再只清理独立
target内的Node包测试缓存，核对新fixture字节并强制重编。没有清理现场程序或数据。

最终source-r2验收通过：先定向复验原3个失败，再跑全部150项Node单测（150通过、
0失败），Node全部target/feature的Clippy（warnings denied）通过；同源Node格式检查
通过。旧测试程序SHA为`736bb008ab426e4771932239ecfd84882da7d98c29854c68e8af15102eb7326b`，
强制新编后的SHA为`91845711a5c5a55159b7a31a9303620ff07ac7b907fac81fba361f5dd4fcbac0`。
源码base archive SHA为`b4c19a4758dc5f689ad1ffcae7499530a62bc22b3ff22817ff5e7c526915bc70`，
source-r2测试修正delta SHA为`32405766972784bc99b55e25a3e294c5498239f8faa42ee66d30908006fa5dec`。
隔离软件目录为`~/rg-lab/explicit-unknown-20261111`，结果为results-r4.json；全仓格式
检查仍如上单独记为未通过，不能将Node包通过扩展为全仓质量门禁通过。

最终中央只读复核：原55项attempt逐值不变，仅新增本轮两项，总数57；狗1Failed、
狗2Unknown，verifier receipts仍空，两个资源仍bound，Controller/MI保持原PID9841/15045。
没有现场部署Node修复、重发动作或以底盘断电事实清除Unknown。

## 证据和后续条件

Windows公开证据根：`C:/workspace/DEAIOS/lab/rg-two-dog-navigation-live-20261111`。
ThinkPad：`~/rg-lab/two-dog-navigation-live-20261111`。原计划、MI命令/审批命令与
响应、原native事件、缓存状态、现场反馈逐份追加保存；私有凭据未导出。
`evidence-index.json`逐文件记录SHA256和字节数。

本轮结果分开记录：接纳/调度链路通过；到达验证未发生；双狗巡检真机效果未通过；
停止/恢复未结案、资源保留。后续动作须先解决原Unknown及恢复合同，再确认新一轮
执行范围和现场条件；不能重用本轮一次性授权自动重试。共享通道、故障接替、照片
和真正编队均未在本轮验收。
