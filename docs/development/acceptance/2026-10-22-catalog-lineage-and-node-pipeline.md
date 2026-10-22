# 原 Catalog、child lineage 与 Node 输入流水验收

本包在唯一分支 `codex/nonrelease-remediation-closure` 接续 `af5191f`；该前置提交的
[远端 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39725135293)成功。
shared wrapper v48 / event codec v29 保护新 Catalog 引用权威，Node 合同不改变。
本包关闭这两个实际生产者的当前实现和故障检查，不关闭完整 N1 或后续 N2–N7。

## 权威与行为

[ADR-0149](../../decisions/0149-catalog-and-lineage-content-references.md)将实际 generic/typed
发布及应用 handoff/MI child 发布的内容引用放入原发布 SQL 事务。引用分别保留实际
Artifact owner、semantic provider、selector、原 manifest digest、bytes digest/size，
并绑定原 tenant/principal/authority/generation/time。每个 child 独立保留原 parent
lineage；父 Catalog 退休不释放 child 的依赖。Spatial 另保存完整原 canonical metadata，
其 frame/anchor/format/producer 不能由 generic discovery projection 替代。

tenant-admin 退休须命中精确原记录及当前权限；原 Mission/use/import/context/provider
revoke 尚未闭合即拒绝。Unknown 和不完整 legacy history 保留。SQL 回滚不安装缓存；
发布和退休重试不刷新原代际/时钟。退休后禁止新发布、准备和导入，审计历史及原内容
权限不因删除目录而消失。最后 Commit 前还独立检查原请求 witness；测试验证已保存的
publication/reference/checkpoint 一起回滚。退休本身不删除 CAS，完整 GC 仍是必需后续。

显式 [recovery v0.10](../../../contracts/recovery/bundle-v0.10/README.md)在原 writer/inode
租约下验证完整引用/发布历史及同一 bundle 的实际 owner/CAS。缺 acquisition、mandatory
table、完整来源或 active bytes 均在发布前拒绝，released history 不要求已删内容。
v48 的无新引用旧合同仍保持兼容；实际 v29 权威不能由旧 profile 验证或遗漏。

## 实际进程证据

证据根目录为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`。运行目录
使用短 `/tmp` 路径以满足真实 Unix endpoint 长度；留存副本保持所有原路径字段和结果，
仅排除实际运行 socket。每次都有原 binary/helper/config 摘要、原 SQL/CAS 与真实退出。

```bash
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-catalog-pipeline-normal-01 \
  --process-workers --artifact-retention --async-workers 1
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-catalog-pipeline-handoff-01 --handoff-case complete \
  --process-workers --artifact-retention --async-workers 1
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-catalog-pipeline-mi-final-01 --handoff-case revision-mi \
  --process-workers --artifact-retention --async-workers 1
uv run python tools/quality/check_catalog_reference_recovery_processes.py \
  --from /tmp/rg-catalog-pipeline-mi-01 \
  --output /data/workspace/roboguide-evidence/nonrelease-closure/p1/catalog-old-writer-recovery-01 \
  --old-controller /data/workspace/roboguide-evidence/nonrelease-closure/p1/legacy-integration-server-45a8241
```

`catalog-pipeline-normal-01/result.json` 验证实际 Node 原发布 acquire、SQL 发布失败后
无悬空目录/引用、活跃 Mission 退休拒绝、原 revoke、退休 SQL 回滚、未消费 HTTP 回包
只读对账、相同命令重试、实际 Controller/Node/backend 重启后不复活，2 个独立复算。
`catalog-pipeline-handoff-01/` 验证 3 次实际 CPU file job、原进程 wait/软件 stop、两次
独立 Task import、原 SQL 回滚/预算及 child/parent lineage。各 owned process 均已回收。

`catalog-pipeline-mi-01/` 保留完整原 Request、实际 MI worker/SQL/HTTP 中断及只读观察、
独立 Review、正常新 Control Group、两次不同 preparation/import、两次真实计算及独立
verifier，最后另核对原 Mission goal。原预算不变。Responses endpoint 是明确协议
fixture，external model calls 为 0；不能由这个结果声称模型规划质量。

`catalog-old-writer-recovery-01/` 实际执行 CLI backup/verify/restore/retry，独立读取
3 份 active 原记录及 4 项 owner/content 依赖，遗漏 lineage bytes 在 publication 前拒绝。
真实旧 Controller binary
`sha256:154f83e414c8ea1015750113bb184911b528ebc0c114264493f1127f5694d461`
对复制的原 current SQL 退出 1，原引用不变；提供新 legacy CAS 路径，排除旧 root layout
fence 抢先拒绝的混淆。旧 profile 对实际 v29 权威拒绝。原结果和命令输出均保留。

## 实际故障发现与输入修复

此前 `rg-catalog-mi-01/02`、`rg-catalog-mi-diagnostic-03` 和 feature-disabled baseline
出现原 verifier 过期或未发送 follow-up slot 被 safety hold。它们没有被改成通过，旧证据
没有刷新；临时诊断 binary 的一次通过也不替代当前正式 binary 的验证。

根因之一是 gRPC session 在每个事实后等应用 Commit，同 session 无法充分进入已有
NodeFactDriver batch。实测失败窗口 heartbeat/state/receipt 排队最大分别约
6188/6182/6121 ms；health age 7195 ms 超过声明的 5000 ms，transport heartbeat 仍新鲜。
[ADR-0150](../../decisions/0150-bounded-node-application-pipeline.md)采用每 session 最多
8 项有界待完成事实：原 wire 顺序同步入队，原 receive time 保留，完成期限在第一次
enqueue 前固定。ACK 按原顺序且只在实际持久 Commit 后发送；后项先完成不越过前项，
拒绝/不可用/超时结束 session，已提交或不确定的输入不被宣称未发生。

`catalog-pipeline-mi-01/` 的同类短窗口中，state ingress 最大 314 us，heartbeat 281 us，
receipt 267 us，health age 1154 ms；diagnostic dropped counter 为 0。两次运行并非严格
因果性能基准，不能把该差值当长期 SLO。保留原 source TTL、lease、Memory budget 和
原 stop gate，没有延长期限或刷新旧 State 换取成功。

合同回归覆盖窗口上限、原顺序及接收时间、提前完成不提前 ACK、首项拒绝/Unavailable/
丢回包、超时不续期及 EOF/error 的实际提交前缀处理。完整 Rust 为 963 passed、2 个
原有 ignored；完整 Python 为 3045 passed、0 skipped（真实 Python 3.9 bridge），
严格类型检查 316 个源文件通过。格式/Ruff、Clippy all-targets/all-features、私有 Rustdoc
与 strict MkDocs 门禁均通过。新增原生产者及冷恢复实际门禁纳入 CI。

## 继续必需

Archive、Controller/Artifact/Raft Snapshot 和在线 Replica 的实际引用生产/退休与内容
闭包、Artifact GC、legacy 未归属 Catalog 的显式迁移，以及完整 N2–N7 继续实施。
此处实际软件 Node 来自每次明确的 file-process profile，设备、模拟器、模型均为 0。
进程退出不是设备停止，静止恢复不是在线 HA，当前没有宣告 SoftwareComplete。
