# ADR-0111：分支集成与独立权威版本冲突门禁

状态：Accepted，2026-10-20。用户授权核对全部分支并将通过检查的产品整改合入 main。

## 问题

Memory 整改与执行故障闭环分别从共同基线演进。两条分支均使用 Node Contract v0.8、
protobuf Execute tag 8、Controller wrapper v32 和 ADR-0106/0107，但含义不同。
仅消除文本冲突会把 Memory preparation 与 attempt generation 混读，或让旧 writer
忽略独立进程退出权威。两条分支各自通过检查不能证明组合后的正确性。

## 决定

保留 Memory 已公开的 v0.8–v0.11、Execute tags 8–10 和 ADR-0106/0107。
执行故障闭环的两份 ADR 顺延为 ADR-0109/0110，记录原分支编号与版本，仅新统一合同
提供组合后的权威：Node Contract v0.12、config v0.13、wire catalog v0.15。
Execute 的非零 attempt generation 使用新 tag 11；含停止声明的配置须显式采用 v0.13，
不得因 metadata 自动改变旧配置的合同。旧 v0.8 不获得停止代际语义，含该声明的旧注册拒绝。

Memory preparation 和 Execute 保存同一个非零 generation，并保留原 invocation digest、
session、provider、deadline 与 current-use gate。重复命令不能换 generation，Node 先持久化
才执行，Memory 准备失败不能绕过 generation 或 epoch 校验。没有停止声明的旧合同仍按
原意义使用；不向旧 Node 推断新能力。

共享 Controller wrapper 升为 v41，Node journal 升为 v12。旧版在任何新写入前拒绝，
包括无事件的 checkpoint 事务。原 Memory v40 可保持原证据、预算与 ownership 迁移；
原独立进程停止分支的 v32 与 Memory v32 意义不唯一，含进程退出权威的旧 wrapper
默认拒绝并要求显式来源核对，不把它自动承认为统一版本的确认。原终态不改写为 Cancelled。

集成只改变合同识别与组合接线。Control 仍独占成员、资源与绑定；Runtime 管理 attempt；
Application 组合 durable gate；Local EAIOS 观察原进程真实退出。软件进程退出不证明设备停止。

Runtime 的 Cancelled 可能归入非成功 Task outcome，但它不成为原始 Failed。自动关闭
SafetyStop hold 的失败收尾必须找到该 Task 的当前原始 Failed attempt、完整停止证明，
且没有 Memory handoff 保留 binding。交接源 Group 在 stop confirmed 后仍保持原所有权，
只能经交接或明确取消/核对流程处理；通用失败收尾不能越过该应用操作的保留门禁。

组合回归另复现 Artifact 的进程写锁问题：原应用最后一个 store/upload 已释放，但无写入权
的子进程仍短暂持有继承 descriptor，导致合法重启报 busy。与现有 Replica store 一致，
Artifact 用共享应用 lease 包装该文件，最后一个 store/clone/upload 析构时显式 unlock。
任何仍被应用持有的 clone 或上传继续阻止第二 writer；子进程继承 descriptor 不获得
Artifact 写入权。故障以实际子进程和保留上传复现，不通过睡眠或放宽 writer 检查规避。

## 验收

检查新旧合同协商、protobuf 独立字段、generation 与 preparation 一致性、错误/缺失代际、
重复命令、checkpoint 恢复与旧 writer 拒绝。组合树运行 Rust/Python 质量门禁，并通过真实
软件 Controller/Node、TLS/SQLite/CAS/文件与原进程退出检查；不启动实验、模型或模拟器。
各远程分支逐一记录 ancestry、patch 等价和范围；无共同历史的独立项目不强行合并。
本记录不关闭完整 F4 或 F5–F10，也不声称 HardwareEvidencePending 已完成。
