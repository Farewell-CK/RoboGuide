# 运行期 MI 与 scoped Memory 整链验收

状态：通用文件软件组合通过；完整跨类型/复杂拓扑与其他非发布整改继续。
源基线 `ed1b9ab`，provider 合同见 [ADR-0128](../../decisions/0128-explicit-memory-plan-provider-dto.md)。

## 实际问题与改动

首次实际组合在发送 provider HTTP 前失败：显式 v0.9 配置被旧 provider DTO 的 v0.8-only
检查拒绝。现在显式支持 v0.9，保留原 semantic Memory requirements、selector 和参数。
默认 v0.8 及 v0.7 actor 输出兼容保持；跨版本输出拒绝。五项新增合同测试验证非空 Memory
往返、nullable omission 和版本降级/越界拒绝。模型不能提供 Node、provider 或路径选择。

实际部署配置还必须提供满意度 freshness policy。本次 fixture 原先没有声明该 policy，
第二次组合正确拒绝；现以明确配置固定原 accepted verifier 的 max age，没有放松校验。
Controller wrapper v45/inner v23、Node journal v13、Memory SQL v3 与当前协议均不变。

全量 Python 2785 项通过，实际 Habitat Python 3.9 兼容门禁启用，零 skip；严格 mypy
275 个文件、Ruff/format、函数文档与 MkDocs strict 通过。Rust 实现与实际 binary 未改动。

## 原始证据

| 固定结果 | SHA-256 | 结果 |
| --- | --- | --- |
| `/tmp/rg-scoped-memory-runtime-mi-20261021-c/result.json` | `bd54055ca833cd71cacfe800b9af125369204a7abf3648e2f98d93e50be36782` | 真正原 Request → 原计算 Failed/独立 wait/撤权 → 非空 child → MI 中断/原 SQL 回退拒绝/候选回包丢失/只读对账 → 独立 Review → 正常新组/两个 import/use/独立 Task 与原目标验证 |
| `/tmp/rg-scoped-memory-mi-complete-recovery-20261021-d/result.json` | `7fc74ee246803cda9cac7150d779619dd9b45e1bedb1355d18ff62716532c22e` | 8 components、13 SQL、96 tables 和原 CAS；3 attempts 明确保留为 1 Failed + 2 Completed，原 Request/MI nonce/Memory/Review/期限不变 |
| `/tmp/rg-scoped-memory-ordinary-mi-regression-20261021-a/RESULT.json` | `66468d856a23773f7cd4313bb3d171dcf371051835ab5e1fd78af20752a81512` | 原无 Memory MI/自动发现/中断/目标验证、实际 v44 → v45 原数据迁移回归 |
| `/tmp/rg-scoped-memory-manual-regression-20261021-a/result.json` | `3bff6268dac08edd816dc062469ba15e6392cfdf6e22f0f442f539e70a43e901` | 原手动 scoped Memory 与各 Task 独立接收回归 |

所有 owned 进程已回收。Controller binary
`87e51b689be71f5cd7f70caa78179f8ddf9930fc57801dddf647999f25c7abe0`、Node
`6128db7f0f2519bbeed14fcc69491cb13b7b3c037679bfe170a1b10d5ce2d73d`；各次源码/配置/
helper/实际 provider wire 和原 SQL digest 均保留。实际命令另经闭合 JSON Schema 验证。

原 Request 在 Node 启动执行前由真实 mTLS HTTP/SQL 引擎创建，未事后编造 Request。
仅明确的 conformance fixture 使用已有可注入 UUID port，对齐预声明 scoped manifest 的
随机 Mission UUID。初始 planner 与本地 Responses 回包均明确为协议 fixture，外部模型、
模拟器、设备与 PhysicalEntity 事实均为零。两台软件 Node 真实注册，仅 source Node 执行
原计算和两个新任务；consumer 不是第二个执行者。两个 provider HTTP 是候选/意见，
Task 与原目标证据来自实际独立文件复算，不能把模型意见当成功。

原 MI workers 退出码 `[-15, 1, 1, 0]`：实际中断、旧 SQL 副本拒绝、实际成功回包丢失、
只读观察后继续 Review。早期 HTTP outcome 保持 uncertain；provider 两个 phase 各一次。
worker 不持有 Execute 权，旧 Group 不自动恢复；每个新 Task 有不同的原 import/preparation。

## 恢复与拒绝边界

恢复逐表核对原 schema/rows，旧 Node/Controller 和活动 writer 拒绝。实际新服务加载完成
Mission，保留完整 Memory 声明、独立 Review 与原预算；完成的 MI 不再出现在原 tenant/
principal 队列。原 provider 当前内容由实际字节重新核验，历史 Imported 不直接变成许可。

scoped 内容在退休后仍拒绝普通 viewer 的裸 CAS HTTP 读取（403），恢复管理员独立核对原
CAS 文件摘要，不新增 grant 或 import 来放行。第一次恢复 checker 沿用了 global 内容的
HTTP 读取假设，因此失败；修正预期而未放松产品权限。第二次恢复 checker 缺少队列的
明确 tenant，仍正确拒绝；现在由原 proposer 在原 tenant 只读观察。中间计数字段曾将全部
attempts 标成 Completed，现分别记录 3 total/2 Completed/1 Failed；最终结果以上表为准。

## 复现

```sh
.venv/bin/python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/unique-scoped-runtime-mi --handoff-case revision-mi --process-workers \
  --old-controller /absolute/archived-pre-v45-controller
.venv/bin/python tools/quality/check_revision_memory_schemas.py --from /tmp/unique-scoped-runtime-mi
.venv/bin/python tools/quality/check_recovery_bundle_processes.py \
  --from /tmp/unique-scoped-runtime-mi --output /tmp/unique-scoped-mi-recovery \
  --old-node /absolute/archived-pre-v13-node --old-controller /absolute/archived-pre-v45-controller
```

没有运行实验或模拟器。此路径不证明外部模型质量、设备停止/身份/交接、跨主机连续性、
typed-map 的完整生命周期、在线 HA 或总体 SoftwareComplete；其余计划仍是必需交付。
