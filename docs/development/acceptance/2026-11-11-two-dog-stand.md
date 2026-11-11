# 2026-11-11 两狗原地站立与路由核查

本轮现场授权为两狗各至多站立一次：先检查实时姿态、电量及本机安全条件；已站立则不
重复起身，明确趴下且本机允许时才执行一次。禁止导航、走动、自动趴下、调速、切图、
拍照、故障注入和自动重试。任务全程使用 MI → RG → 对应 Node → 本机 Robonix。

本轮是两条独立的单狗姿态任务。尚未执行同一 Mission 的双狗分区巡检、共享通道竞争或
故障接替，不将这些模式算作已验收。

## 结果与边界

- 狗1：RG/原生任务以 execution-report 结算 Completed/SUCCEEDED；原生六条事件没有
  plan 或站立 call，最终文本明确表示当前状态未知而不调用站立。用户现场确认完全没起身。
  这次物理站立目标**未达成**，不能以任务 Completed 宣称狗1站立成功。
- 狗2：原生三个 plan 包含一次 `chassis/stand`，调用前只读状态为 down，调用后原生
  `chassis/state` 为 standing。用户确认起身，随后为省电手动趴下。后续 down 与该现场
  解释一致，不能把手动趴下记成 RG 自动趴下或本次起身失败。
- 未发现任务串狗：各本机只读 SQLite 中恰有自身 attempt，对方 attempt 不存在。
  这证明本轮 RG 路由及本机接纳归属；更下游 ROS/DDS、底盘网口与设备序列身份尚未
  独立审计，不把它扩大为任意部署下或所有物理通信链路的身份安全证明。
- 最终中央快照 Unknown=0、allocations=[]；后续从带验证的 v3 重建读取中摘录了两条
  原任务的调度选择、Commit、Bind、execution-report结算与本机任务槽释放事件。
  这些原事件证明本轮中央事务流水，空占用仅作为其最终快照佐证。
- 两任务均沿既有 execution-report 结算，未部署独立姿态目标 verifier。保留原生命周期，
  物理效果另按本机原回执、RG 后续 State 和现场观察判断，不事后改写原记录。

## 请求、计划、分配及本机执行

| 项目 | 狗1 | 狗2 |
| --- | --- | --- |
| MI Request | `request-a9503f375cde49e3b38eccd6a908bb42` | `request-d7f257729a914f37ad018be8c35b913a` |
| MI 草稿 | revision1，AwaitingApproval | revision1，AwaitingApproval |
| 草稿摘要 | `sha256:4bb07c4334c0619c5c61b8c6ec508f787ea0672b607e8d2c36e1ca2e833018c5` | `sha256:873bbbc3278533346eb19d737c15b673c999ae973f68dd902f9212949986afdb` |
| 本轮批准后 | Accepted | Accepted |
| Mission | `mission-3b5d831733be4820938b784f78eeb2f8` | `mission-aa86bf7d9dde4f958aed6b868654dd89` |
| Task／Role | `stand-once`／`stand-role` | `stand-and-report`／`stand-role` |
| 中央 attempt 的 Node | `lite3-dog1` | `lite3-dog2` |
| RG／本机 journal 状态 | Completed／SUCCEEDED | Completed／SUCCEEDED |
| 原生 session | `f6ee6992-f17e-4082-82fc-12feefca45bd` | `541863a8-d0e3-468b-92a0-cb0c1f97f786` |
| 本机原事件数 | 6 | 33 |
| 原生站立调用数 | 0 | 1个不同的call ID |
| 原生趴下调用数 | 0 | 0 |
| 物理观察 | 用户确认完全没起身 | 用户确认起身，之后由用户手动趴下省电 |

狗1原 attempt：

```text
attempt-46:group-mission-3b5d831733be4820938b784f78eeb2f8-10:stand-once-10:stand-role-1
```

狗2原 attempt：

```text
attempt-46:group-mission-aa86bf7d9dde4f958aed6b868654dd89-16:stand-and-report-10:stand-role-1
```

任务绑定的本地资源分别为 `dog1-pilot-task-slot`、`dog2-pilot-task-slot`，generation均为1；
原始请求中没有向本地运行时授予额外资源或授权它重选参与者。

## 原调度、Commit／Bind 与资源释放事件

原始出处为 `stand-original-control-events-1794363265174693230.json`，记录只读 SQL 时间窗
`[1794362396000, 1794362701000]`，共4121行。读取使用已验证的 v3 重建函数，而非直接把
正文引用当完整正文或重算历史；每个匹配原事件保留序号、事件ID、时间、关联与因果ID、
原 payload schema、原 payload SHA256及解析值。这里只摘录两条已存在任务，不写数据库。

| 原事件 | 狗1原序号 | 狗2原序号 | 该层证据 |
| --- | ---: | ---: | --- |
| TaskSchedulingSelected | 2277649 | 2280226 | 分别选中 lite3-dog1／lite3-dog2，stand-role与本狗任务槽 |
| PlanCommitted | 2277651 | 2280228 | 对应原 Task 的计划提交 |
| ExecutionGroupBound | 2277652 | 2280229 | 对应原 Group／Task 完成 Bind |
| MissionActorBound | 2277653 | 2280230 | actor-dog1→lite3-dog1；dog2-actor→lite3-dog2 |
| TaskSatisfied | 2277824 | 2280840 | basis均为execution-report，不是独立物理目标证明 |
| TaskExecutionBindingsReleased | 2277825 | 2280841 | resource_ids分别包含dog1-pilot-task-slot／dog2-pilot-task-slot |

狗1原调度、Commit和Bind均记录于 Controller时间 `1794362396596 ms`，任务报告结算及
槽位释放为 `1794362409344 ms`。狗2对应时间为 `1794362588686 ms` 和 `1794362633790 ms`。
这些均为原 Controller 时间，未与源设备或采集机时钟相减。

狗1释放事件原payload SHA256：
`69f692e659e81ef169acb344ccacec690a23c07ceb3c4b8e099cbf76b915713e`。
狗2释放事件原payload SHA256：
`7f463e2a706da5d5ae119ae8445f1c35aa11f425f176dc67d0d93f4f6bc2e1c9`。

狗1即使没有站立调用也发生了execution-report结算和资源释放；原流水完整不代表物理
站立成功。该结果保留为目标验证仍须补齐的反例，不更改原历史或追加动作。

## 双向核查本机 SQLite

只通过 `sqlite3.connect(file_uri + '?mode=ro', uri=True)`、`PRAGMA query_only=ON` 和只读
事务查询两个精确 attempt。未实例化会更新状态的 `Journal`，没有调用状态重算、取消、
重试、操作机器人或重启服务。

| 本机数据库 | 狗1 attempt行数 | 狗2 attempt行数 |
| --- | ---: | ---: |
| 狗1 `~/rg-lab/two-dog-patrol-20261110/pilot-journal/pilot.sqlite3` | 1 | 0 |
| 狗2 `~/rg-lab/delegate-20261104/task-journal/pilot.sqlite3` | 0 | 1 |

两条本机 run 均 dispatched=1、cancel_requested=0、abort_sent=0。dispatched仅证明该原
任务越过委派边界，不表示它必然产生了物理动作；狗1就是未调用站立的实例。

证据导出保留每条原事件 raw 的 SHA256与长度、decoded 原字段和provider；凭据字段与
凭据形态的字符串值脱敏。raw摘要针对数据库内原字节，不针对脱敏后投影。敏感原字节
没有另行回显或加入此文档。

## 狗2原生调用流水

本机 session为 `541863a8-d0e3-468b-92a0-cb0c1f97f786`，Liaison原 registration为
`f5acdf8f-a6a7-4db7-9347-87f5c23d20b7`。事件序号指本次 journal handle 内的原序号。

| 顺序 | plan／call | 合同及provider | 原结果 |
| --- | --- | --- | --- |
| seq7计划、seq8结果 | `770b-1`／`770b-1:0` | `robonix/system/executor/builtin/read_capability_doc`，executor | 读取底盘能力说明 |
| seq15计划、seq16结果 | `770b-2`／`770b-2:0` | `robonix/primitive/chassis/state`，lite3_chassis | down，电量54%，source stale=false、age_ms=8 |
| seq23计划、seq25结果 | `770b-3`／`770b-3:0` | `robonix/primitive/chassis/stand`，lite3_chassis | success=true，原输出报告standing |
| seq23计划、seq26结果 | `770b-3`／`770b-3:1` | `robonix/primitive/chassis/state`，lite3_chassis | standing，电量54%，source stale=false、age_ms=0 |
| seq32／seq33 | 原生task_state done／final_text | 同原任务与session | 报告完成一次站立，明确遥测不是独立物理验证 |

seq29 batch_result再次包含 `770b-3:0` 的同一结果，不是第二次站立调用。按不同call ID和
原plan/result归属计数，不能按事件中出现“stand”的次数计数。

原stand结果事件seq25 raw SHA256：
`4fc6af2e29a19571fe864ae45b4f5eb683018ed3445ff505b72c6944b2cdd07b`。
原后续state事件seq26 raw SHA256：
`455d19b14ffd13026c4e131131de906a340a575c92fb2e1e2c5036f15a7716ac`。

狗1原最终文本seq6明确拒绝在未知姿态时起身，raw SHA256：
`96d7a4044f10cf9ce78cadb74c1ae0d2d57f187997fd4a514e0bdd7fbddcf882`。
没有将这个拒绝解释成硬件动作已经执行。

## 最终 State 与原证据

`stand-observation-1794362699599605138.json` 同时保留两 Mission、中央 attempt归属、
admission／tenant回执、State、姿态评估、空占用和Unknown=0。该快照两狗均报告down；
狗2需要结合用户手动趴下这一现场事实理解，不用事后down覆盖此前standing原回执。

证据目录：`C:/workspace/DEAIOS/lab/rg-two-dog-patrol-20261111`。

- 原MI草稿和批准结果：`dog1/2-stand-draft.json`、`dog1/2-stand-approval-result.json`。
- 中央结果：`stand-observation-1794362494129015834.json`、
  `stand-observation-1794362699599605138.json`。
- 双向本机归属：`dog1-exact-stand-attempt-audit-1794362815221885400.json`、
  `dog2-exact-stand-attempt-audit-1794362815370249300.json`。
- 原调用提取：`two-dog-stand-identity-summary-1794362969048150900.json`。
- 原中央事务：`stand-original-control-events-1794363265174693230.json`。
- 狗1先前独立导出：`dog1-original-stand-native-events.json`。

下一步应先保留狗1此次“原生不调用、物理未起身”的证据，再明确本机状态读取与任务
目标达成的责任边界；不在本次只读审阅中追加动作或悄悄重试。双狗巡检继续等待各阶段
原合同和当轮运动授权，不能以本轮单狗站立链路代替。
