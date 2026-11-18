# 2026-11-05 狗2返程、断电取消与换电后新任务

去公司门口的到达已获[现场确认](2026-11-05-dog2-company-entrance-navigation.md)。
用户随后要求回到起点；本记录保留返程中断和换电后的新任务，不把送达解释为到达。
RG 发送目标并跟踪原任务；本机导航与安全由 Robonix 决定，没有修改 Robonix 内部实现。

## 断电前任务

首条 `dog2-nav-return-start-20261105T084116Z` 目标附带详细的本机核验要求。
Robonix 只执行了状态查询后拒绝导航，RG 收到 Failed 并释放占用。用户随后明确要求
不要介入 Robonix 内部细节，后续目标改为返回已记录的地图起点。

`dog2-return-origin-20261105T084720Z` 已发起返回原起点的导航；用户报告
“停，狗没电了 我换块电池”。RG 受理取消，断电后原 attempt 为 Unknown，Mission
为 Cancelling，保留 `dog2-pilot-task-slot`，没有把掉线解释为任务结束。

## 换电后的原任务结案

用户确认换电并明确授权新返回任务。实际 Thor boot 从
`fc18215e-37eb-4ddf-9992-a343836fc336` 变为 `4954f35d-8ed4-45cf-ac8d-3b2de60e0c5c`，
Liaison registration 同时改变；RG Node 在线，电量约 95%。

通过已有离线本机管理员工具，核对原 handle
`a72eb979-305b-4cf0-a7b5-1cb3c3f60331`、原请求摘要、sequence 27 及全部 18 项原事件
摘要后结案。原记录已请求取消并发出一次 scoped abort。实际本机主体为
`local-os:localhost.localdomain:nvidia:1000`，随后原 Node 将 Cancelled 回传 Controller。

Controller 通过原 mTLS 管理员结案接口处理同一 attempt。原 Mission 成为 Cancelled，
占用和 Unknown 归零；原请求时间、停止 deadline 和原事件保留。两项回执均明确
`physical_stop_confirmed=false`、`automatic_continuation_authorized=false`。
这不是物理停止证明；下述新任务依据用户另一次明确授权，经正常 Control 提交。

## 换电后的新返回任务未完成

- Mission：`dog2-return-after-battery-20261105T090824Z`。
- Handle：`167cd004-7372-4bc6-8a46-18b1171222ff`。
- Session：`48103aed-5964-4f39-a339-20bf508fdc51`。
- 原 turn：`115fadb1-80ad-4766-9bd9-f66037534ccf`。
- 目标：原地图 `1101_visual_dog2` 的出发点 `(0.767230, 0.448886)`，yaw 约
  `-0.237019`；目标要求沿用此前低速上限 `0.16 m/s`。

验证通过后只发布一次，Controller 返回 202，原 Node 与同一 Connector 受理。
保留的原生 Plan 正文只有 `432e-1`：读取导航能力文档和当前速度限制，然后保持
“Planning the next step”。后续任务状态还引用 `432e-2`，但原流没有收到其 Plan 正文；
不能由缺回执推断不存在后续副作用。换电重启后只读限速报告为 60% 即 `0.48 m/s`；
尚未收到设置低速或导航调用的回执，不能宣称本轮低速约束已落实或已经返程。

约 300 秒原预算结束后，执行进入 Unknown；适配器对原 session 发出一次 scoped abort，
仅收到“stop requested”回执。Controller 随后受理本次超时 Mission 的取消。
最后已保存的状态为 Mission Cancelling、attempt Unknown，原占用继续保留。
这不是成功、设备停止确认或管理员再次结案。观察位置仍约 `(7.493128, -2.971106)`，
未确认回到原起点；没有为绕过未决占用再次下发任务。

## 后续单独授权

用户随后明确要求先站立再返程。实际读取当前 Executor 活动计划为零后，通过同一
离线管理员工具及 Controller 认证结案接口结束上述原 Unknown，原记录、事件和
deadline 均保留，不生成 no-effects 或物理 StopConfirmed。新的站立、返程及后续
趴下另见[本轮记录](2026-11-05-dog2-stand-then-return.md)，不覆盖本次超时。

## 证据

ThinkPad：`~/rg-lab/cancellation-20261105/return-after-battery-20261105/` 及各原 Mission 目录。
Thor：`~/rg-lab/return-after-battery-20261105/`，保留原行、原事件字节、本机结案命令、
回执与观察。原 SQL 备份位于独立 private 目录，不导出至公共证据包。
