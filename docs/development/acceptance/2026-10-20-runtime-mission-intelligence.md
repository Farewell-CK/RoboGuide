# 故障触发 MI 工作、原调用与恢复去重验收

设计：[ADR-0121](../../decisions/0121-durable-runtime-mission-intelligence-work.md)；
[接口与上界](../runtime-mission-intelligence-v0.2.md)。本项实现显式 v0.2 生成授权、原 SQL
claim/phase nonce、真实 Request/admission 绑定、完整原回包、独立实际 Review 与安全切换。
自动发现、scoped Memory 迁移和 F0/F4–F10 其余必需项继续实施；不关闭完整 F8。

## 实际过程与证据

Controller binary SHA-256：`e6103473161a279f52feda882409fa653aaa6aaa26da22e5c9c43bec0bb86995`。
Node：`6128db7f0f2519bbeed14fcc69491cb13b7b3c037679bfe170a1b10d5ce2d73d`；
Replica：`d51f46407b39faeaa3481afdb9d396110cb7016446aa4e03bf565513cdaa3242`。

```sh
.venv/bin/python tools/quality/check_plan_revision_processes.py \
  --output /tmp/rg-runtime-mi-generation-20261020-f --skip-build \
  --old-controller /tmp/rg-before-automatic-generation-controller-20261020 \
  --automatic-generation
```

实际 mTLS Mission Request 服务先通过生产 engine/SQL/HTTP 向 Controller 提交原计划，
worker 校对其真实原 admission bytes。初始 planner 和 loopback Responses endpoint
明确是协议 fixture；Catalog 是原实际 CPU/file workflow 的部署声明，不登记物理实体。
外部模型调用、simulator、设备与物理实体均为零，不据此推断模型效果。

一个实际 Node 与四个 owned CPU children 的退出结果为 `[-15, 7, 0, -15]`。取消已接收
但原 child 尚活着时切换被拒绝；实际退出后手动版本切换成功。下一 child 真实 exit 7
触发新生成 ticket；完整 stop/revoke 后才允许 claim 和调用。生产 Responses parser、
provider transport、SQL journal 与实际 proposer/reviewer HTTPS 都被执行。

在实际 provider 回包/SQL commit 后中断 worker，保留原完整快照；恢复真实调用前的较早
SQLite 快照，重新启动的 worker 被 Controller 409 拒绝，没有再次访问 provider。
保留这份失败副本，再恢复原完整 SQL，丢弃 Controller 已成功提交候选的真实 200 回包。
新 worker 只读查询原候选，将观察另存；原 HTTP 仍 uncertain，没有重发候选或模型调用。
实际独立 Reviewer 对 exact digest 批准后才切换版本并启动最终 CPU child。

四个实际 MI worker 进程退出为 `[-15, 1, 1, 0]`，分别对应受控中断、旧快照拒绝、
真实回包丢失和最终恢复完成。协议 fixture 总共只收到两次 HTTP 请求：一次生成、一次
语义 Review。SQL 的 work/kernel/原期限逐字段不变，generation phase 原 nonce 不变。
F 的实际 MI implementation code digest 为
`sha256:dec0315051136f5e553f89f9661ec281ad4ace6f17fc6665b5bc2822f69f97a3`，
原 SQL evidence JSON SHA-256 为
`19c21ea570ff740a3269c94480aefdfc8fb287569f436fce8681e95cd865823c`。
RESULT 同时绑定配置/Catalog/Prompt/依赖、实际 provider wire history 与 checker source。
SQL 中 `candidate` effect 保持 uncertain，独立 observation 单独保留；全部实际请求和
原始 bytes、原回执、进程日志、旧/完整/回归副本均保留在验收目录。

最终 child 实际读取文件并计算 SHA-256；独立进程重新读取并核对。Task 全完成后 Mission
仍 Running，只有独立 producer 的原目标 evidence 才完成 Mission。普通 resume、取消
等待原 child 退出、取消后不执行新 Group、原预算与多次 Controller restart 均再次验证。
实际 v43/inner v23 零 Node 数据迁移到 v44，原 plan/admission/accepted_at 不变，旧 writer
被拒绝。unit fixtures 另验证已有 policy/history 与新增权限伪装/nonce 篡改拒绝。

| 目录 | 组合核验 | RESULT SHA-256 |
| --- | --- | --- |
| `/tmp/rg-runtime-mi-generation-20261020-f` | 原故障/停止、MI SQL/旧副本/回包丢失、实际 Review、原目标/取消/重启/旧 writer | `f77d1657aed656c49a4c210ce039840ee21c4094895c2781d7afdb84998dc044` |
| `/tmp/rg-runtime-mi-identity-20261020-a` | 单 worker、真实 TLS/四连接容量、排队撤销与零 Node identity 证据 | `a786dde60710b4d0b74703a6ac26b7dcf1f5514b9473e59df53f14db7ca8db55` |
| `/tmp/rg-runtime-mi-memory-20261020-a` | 双软件 Node、真实文件/import/CPU/独立 verifier、revoke/reclaim、两次 SQL 故障与原生告警 | `8243b531d57d5efbad1002f973736067bee6c3116240cc989ac196847ac4bbc0` |
| `/tmp/rg-runtime-mi-certificates-20261020-a` | 三实际 Replica、同成员 leaf/CA 轮换、Abort/原 store restart 与 owned cleanup | `7213d86a25abbb272f399ef0f63c8075d2b71e0e5b24ffd0d82e43d35010c664` |
| `/tmp/rg-runtime-mi-cold-recovery-20261020-a` | 六原 SQL/39 tables/十条换证回执、CAS 新目录、三个实际 leader 读与 owned cleanup | `731a173e2065edb9768a1175931eb606a87a29389c18a0b58ff6fd4daa568a84` |

## 已保留的反例

A 检查在新测试调用未带实际必需 Idempotency-Key 时被入口拒绝；B 检查暴露软件 operation
未加入配置 Catalog；两项均在 fixture composition 修复，没有放宽生产认证或 Core Catalog。
C 暴露 Controller normalized export 省略 `verifier: null` 与严格 Python 解析不兼容。
明确兼容读取保留原 raw export 和 authority digest，补 nullable syntax 后 D/E/F 通过；不能
以重新计算历史 plan digest 修复迁移。A/B/C 失败目录与日志均原样保留。

真实畸形 HTTP response 的原 bytes 在解析失败前已保存；重启再次读取时没有第二次 HTTP。
实际 SQLite contention 超过原预算后 callback 未执行；独立 reader 在网络前看见 pending
intent；硬链接第二 writer、配置/输入替换和 inode 替换均拒绝。上述为明确软件/unit 边界，
不制造设备能力、物理停止或硬件连续性。

完整 workspace 917 passed / 2 explicit ignored；Python 最终全量 2705 passed / 1 skipped，
另有原 source read/SQL 等待与跨语言读取针对性回归。Clippy/private Rustdoc `-D warnings`、
Ruff/format、strict mypy、函数文档、真实 v0.2 命令 schema 与严格 MkDocs 均通过。
实际日志以 `/tmp/rg-runtime-mi-` 为前缀。
