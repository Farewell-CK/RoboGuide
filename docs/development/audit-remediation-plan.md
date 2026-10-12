# 审计优化实施记录

本记录跟踪 2026-10-12 经复核的 O1–O8 优化方案，历史只读方案和 Claude 原报告保留原样。
实施起点为 `dev@7eab30d51bd60f0d3c06e8397b788211ea8a8e9f`。

| 工作包 | 状态 | 验收证据 / 下一步 |
| --- | --- | --- |
| O1 恢复定位硬化 | 已实现 | `7eab30d`；相关包回归通过。历史重复 Role 根因仍为 NOT PROVEN |
| O2 所有权模型 | 已实现并验证 | Control 137 个常规测试；256 条固定轨迹及 10,000 条扩展轨迹通过，支持 case 缩减、保存与回放 |
| O3 配置与只读指标 | 已实现并验证 | ADR-0075；统一 timing、自定义 new/renew/restore/Welcome、两级 queue 及 default-off JSONL，观测失败不改变 durable trace |
| O4 时间与 Control 活性 | 已实现并验证 | ADR-0076；Control 验证连续 exact-session evidence，独立双时间 ingestion、失效/restart fence；workspace checks 通过 |
| O5 Mission 故障隔离 | hold/回滚/显式恢复与 deadline 运行期局部分类已验证 | ADR-0077/0079；真实 timer/HTTP/SQL 路径证明 exact-Task 子集；其他未知错误仍 fail closed |
| O6 事务性能 | 有界 batch 已验收；增量存储软件完成并保持 opt-in | ADR-0080/0081；真实协议轨迹等价、batch Commit/serialization 次数减少；delta 写入 bytes 减少但吞吐收益未建立；full 为默认，原始失败保留 |
| O7 部署边界 | 已实现并验证 | 具体 source / neutral codec / consumers 分离；137 个 app tests 保留兼容与 provenance |
| O8 发布技术准备 | 技术核验已完成；发布决策待定 | 历史扫描 23 提示（1 条凭据风险待处置）、依赖元数据、ADR-0078、4/4 quickstart、认证边界披露 |

E6 尚未开始并持续暂缓。本任务只使用确定性离线测试及独立临时数据库、端口的本地合成
gRPC 测量，不修改 E6 harness、配置、产物，也不启动模型、Habitat 或真实设备。
无关未跟踪内容保留。

每包独立提交并验证远端 SHA。验收区分实现、确定性检查、合成进程测量和真实物理证据；
后两者不能互相替代。性能结论记录源 SHA、冻结输入、重复次数、样本数、失败项和原始
阶段耗时，不能从旧 pilot 归因当前版本。

## 实施基线质量门

上一切片遗漏 workspace Clippy；基线检查发现 `NodeLeasePolicy` 两个私有字段缺少文档。
补齐字段注释后 `cargo clippy --workspace --all-targets --all-features --locked -- -D warnings`
通过。O2 workspace tests 与 Rustdoc 也通过，不以相关包测试代替。

## O2 交付

加入反例后先观察失败，再补齐 recovery release 前置校验、legacy Role 唯一性及 pending
recovery 的 exact-unbound/previous-owner restore 校验。合法路径的事件次序及 checkpoint
shape 不变。此证据不证明 Claude 对历史 pilot 的根因归因。

相关 Control Clippy 和 tests、workspace tests、Rustdoc、formatting、diff 检查通过。扩展
检查 10,000 条轨迹耗时约 58 秒；只表示本地确定性模型覆盖，不是性能或硬件实验。

## O3 交付

新增 `NodeProtocolPolicy`、original ingress metadata 和传输中立诊断端口；部署 root 读取
startup 配置、共享 process Clock，并注入默认关闭的本地 JSONL sink。指标有界聚合、
try-send、独立 I/O thread 和两份文件轮转。格式、workspace Clippy、workspace tests、
Rustdoc 与 diff 检查通过。custom loopback 协议、acceptance timeout、health freshness、
checkpoint restore 及 disabled/full/failed sink 的一致轨迹测试通过。

## O4 交付

reader 连续摘要经传输中立端口进入 Control；timer/consumer 在原 durable candidate
事务中接受 liveness-only evidence，不更新 health、不提前 Ack raw heartbeat。记录初始
session anchor、sequence watermark 与 fenced expiry，restore 同 live lease 清空。
registration lease 使用当前 decision time；其 Offline 状态及后续 State/health/peer/progress
保留实际 receive time。新事件 codec v13 可读取 v2–v12；旧 checkpoint 仍兼容。

连续/真实静默/错误身份/顺序/重放/expiry/route-loss/restart、延迟 health 与 custom
loopback metadata 检查通过；workspace Clippy、tests、Rustdoc 与 formatting 通过。
此实现与合成证据仍不能证明历史 pilot 故障的因果或设备物理停止。

## O5 交付

ADR-0077 定义保守的 Mission execution hold。Ready dispatch / terminal outcome 使用独立
candidate 和 SQLite savepoint；只有 typed InvalidContract 且 trusted baseline 验证通过
才隔离。Control/未知/SQL 错误仍 fail closed，不宣称全部 fatal 已消除。

隔离保留 raw facts、当前 attempt、资源和 Context ownership。Execute outbox、派生
outcome、自动 recovery 和 verifier satisfaction 均暂停；取消继续使用原 draining 规则。
显式恢复按 generation 验证 accepted plan、Control 资格规则、registry/ownership 和
当前 attempt，再由同一事务 checkpoint/commit。旧 wrapper 防止忽略新增 authority；
event codec v14 保持历史读取。

验证：workspace tests 627 passed / 2 explicit ignored；8 个隔离专项测试通过。
SQLite append/checkpoint/savepoint 故障、HTTP 400/409/503、持久重启、Cancel draining、
不重放 Execute 已覆盖。workspace Clippy、Rustdoc、formatting 和 diff 检查通过。

## O6 交付

真实 loopback Controller + synthetic smoke 六轮共 96/96 完成，原失败轮 13/96 保留。
已提交 smoke 精确重投去重修补，新增有界测量入口与分阶段证据、batch 实施门禁和
增量持久化独立设计。未启用 batch、降低 durability 或启动 E6。详见
[事务测量与设计](controller-persistence-performance.md)。

## O7 交付

具体 reset/endpoint/operation 解码、通用 digest、neutral verifier source codec 与
中性候选/plan/attempt/satisfaction 消费者分别成模块。Core 和实际 adapter 权威未改变，
源字段、digest、默认关闭、未知和 legacy profiles 保留。详见
[部署证据边界](deployment-evidence-boundaries.md)。

O7 验证：workspace tests 628 passed / 2 explicit ignored、workspace Clippy、Rustdoc、
formatting 与 diff 检查通过。此处的代码组织改变不声明新分布式能力或物理证据。

## O8 交付

Gitleaks 8.30.1 校验官方归档后扫描本地全 refs 可达历史（561 commits）与 tracked HEAD；
历史 23 提示，22 个 response cache 标识和 1 个旧功能分支硬编码凭据 fallback。
当前 HEAD 0 提示不代表历史风险已关闭；真实凭据有效性和失效处理未执行。
当前锁定 Rust/Python 依赖的许可证元数据已盘点，项目 LICENSE 保留负责人选择。
重复 relocation ADR 改为 0078；78 个 ADR 无重复。声明式 validate 与真实 loopback
最小 quickstart 4/4 完成，网络/认证/物理边界已披露。详见
[发布技术准备审计](release-preparation-audit.md)。

## 实施后 review 修补

复核基线为 `dev@879a5be`。两个反例均先在未修复代码上失败，再在生产路径修补后通过：

- Group continuation 的应用准备入口加入 Mission hold 检查，覆盖 timer 和 ingress 共用
  路径。两个 current attempt 在隔离时保持两个，不提交 replacement；原 repeat 授权、
  Cancelled facts、reservation 和 105ms deadline 保留。显式 generation-bound resume 后
  才允许原预算内 continuation；预算到期不创建新 attempt。durable restore 后重新登记
  当前 topology/health 仍受 hold 约束，不将过期 registration 当作新证据。
- Peer ack 入库后按当前 decision_at 刷新 Runtime fence，保留原 received_at/expiry。
  延迟新 sequence 和 exact replay 不复活过期 Ready；下一 tick 前 checkpoint 为 Fenced。
  一个 peer 更新不能刷新其 partner，全部当前 ack 新鲜时正常恢复 Ready。
- 对 O5 验收结论作纠正：新增真实 HTTP admission + timer 测试确认 400/409 拒绝、
  内存/事件/checkpoint 不变及已有 Mission 继续 dispatch。人工 InvalidContract 注入
  只验证 savepoint/hold 机制，当前 accepted-plan dispatch/outcome 尚无已验证的该类型
  生产来源。运行期自动隔离分类仍为明确的后续门禁，不能扩大捕获所有 Control/未知
  错误来伪造覆盖；README、V2、ADR 和故障矩阵保留这一限制。

修补验证：workspace all-features tests **631 passed / 2 explicit ignored**，workspace
all-targets/all-features Clippy（warnings denied）、Rustdoc、formatting、diff 检查与严格
文档站构建通过。所有检查均为离线/本地合成证据；E6 未开始，未运行模型或硬件。

## 后续 S1 与 ADR 核验

ADR-0079 关闭上节的首个运行期分类门禁：Runtime 保存不可变 first terminal received_at，
Orchestration 检查 accepted Task completion deadline；生产 timer 持久隔离 A 而 B 完成。
5 项新增 timer/HTTP/真实 SQL 检查验证 ownership、延迟处理、replay、历史时间缺失、
resume conflict 与失败原子性，另有 Runtime 时间不可变/restore 检查。该时限是 Controller
接收契约，不是物理时钟或停止证明；其他错误 scope 仍不能推定为 Mission-local。

已实现而旧状态未更新的 ADR 已回写原文；完整状态与待实施依赖见
[ADR 遗留项清单](adr-follow-up-register.md)。E6 保持暂停。

## Dev review follow-up（2026-10-12）

针对外部 dev review 的确定性软件缺陷已补入当前开发基线：

- ADR-0085 为 replicated Controller 的 `Execute`/`Cancel` 增加可选 issuing epoch；Node
  execution journal 持久化最高 `(term, replica_id)`，旧 epoch 在任何 Local EAIOS 调用前拒绝；
- live Node route 不会再被尚未完成 application acceptance 的重复注册驱逐；
- 全部输入均被拒绝的事实批次也会在 rollback 后 fence 对应 session；
- HTTP/Artifact listener 停止进入 Controller fatal 通道；Node owner-mismatch 和 terminal
  conflict 使用 typed `FactRejected`，未分类 Runtime/Control/SQL 错误继续 fail closed。
- quorum `Commit` 的异常响应先回读同 issuing epoch 的 accepted application identity；精确匹配
  时继续本地提交，无法确认时回滚并退出到 replica supervisor 重建路径。

这些修补没有扩大复制 profile 的边界：当前仍是同机 trusted-loopback 软件复制，没有
mTLS、跨主机 membership、物理 stop proof、历史归档/压缩、完整 Mission-local isolation、
backfill/空间资源图或正式 E6 证据。专项 workspace checks 通过后，原 dev review 中的这些
项目继续按 [ADR 遗留项清单](adr-follow-up-register.md) 保留。

## Dev review follow-up implementation (S8–S10)

后续切片已把审计中剩余的三个软件边界落到当前开发基线：

- ADR-0086 增加 opt-in Node mTLS。Integration Server 在 Hello 前后绑定 TLS client
  certificate digest 与 NodeId，Node Service 支持对应 client profile；replica RPC 增加可选
  envelope secret。默认 trusted loopback 兼容路径仍明确标注为开发模式。
- ADR-0087 将 replica metadata history 从硬拒绝改为 checkpoint-anchored compaction。
  `HistoryAnchor` 保留前缀 sequence/digest，完整 checkpoint 仍是恢复 authority；空 tail、
  snapshot/reopen 和旧格式 downgrade fence 均保留。
- ADR-0088 为 Mission Hold 增加 review deadline/escalation evidence，并将每个 gRPC session
  的 validated reader ingress 改为固定有界队列。SLA 只产生告警状态，不能自动释放、重试或
  重新绑定；队列背压不改变应用 acceptance 和 ACK authority。

三机器人 Controller 崩溃目标作为离线故障矩阵验证：失联期间 Node 不接受旧 epoch 新命令，
重启使用 checkpoint/replica identity，失联 Node 保持 Unknown/RecoveryRequired，资源未知时
拒绝冲突性 Commit，恢复后仍需 Match → Propose → Commit → Rebind。该矩阵是目标行为的离线
证据，不是物理 stop proof，也不启动 E6。
