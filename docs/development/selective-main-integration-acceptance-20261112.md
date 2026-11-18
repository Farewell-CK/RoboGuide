# 通用能力整合验收 · 2026-11-12

按[选择性整合计划](selective-main-integration-plan-20261112.md)完成五批通用代码整合。
本记录属于隔离源码/软件进程验收，不是现场部署或长期稳定性验收。

## 来源与范围

| 批次 | 集成提交 | 固定来源与结果 |
| --- | --- | --- |
| 通用接入与启动基础 | `96a396a1` | 真正 merge `7fd7b2d1`，保留 chat、双方 README、完整祖先；增加 Request v0.5 / GroundingContext v0.4 兼容桥 |
| 存储治理与恢复 | `db0cdbb6` | 真正 merge 至 `516dfdf4`，保留完整 v3 / recovery v0.32 链；不包含后续照片交接 |
| Node Unknown | `98916053` | 选择性移植 `99da6d5a` 的八个 core 文件、配置片段与 ADR-0250 |
| 通用 Scene / physical World | `220a8dfd` | 从 `a9f1268b` / `f686ce22` 抽取 producer、Connector、MI 校验、合同与测试；按较早 Connector 基础接线 |
| host-owned objective | `bc0cdfa1` | 整合 `200e7a4e` / `1c2afd67` 的新增逻辑；显式 profile、原 Prompt/调用预算与精确执行保持 |

集成兼容修复另有 `3ed0fe90`（仓库检查发现独立姿态源）、`05a14eb9`
（独立适配器 CI/职责文档）及 `443d3c29`（真实空 legacy Artifact 的完整备份恢复）。
host objective 的集成 ADR 为 0255，不覆盖原接入 ADR-0231 或 chat ADR-0251～0254。
当前 main 的无输出上限与 Catalog rejected-draft 恢复规则保持。

固定双狗 assignments、巡逻预检/导航模板、人工 arrival verifier、照片交接模块均未加入。
H-CoRE、E1 历史发布、Android/NPU 和独立应用树继续单独保留，未删除原分支或实验资料。
选择性移植不使源分支成为 main 祖先；不能以 Git 分支列表替代内容交付表。

## 组合与专项检查

- Rust workspace Clippy（all-targets/all-features）通过。
- Rust workspace：**1359 passed、2 ignored、0 failed**，含 Node、State、Controller 与恢复测试。
- Python 合并组合：**4329 passed（780.32 秒）**；在最后空 Artifact 局部修复前启动。
  该修复后原有及新增恢复专项 **34 passed（25.50 秒）**，其中 13 项为新回归。
- 独立 Robonix adapter：**91 tests passed（19.461 秒）**；严格类型检查 15 文件通过。
- 此前治理 recovery 包 **433 passed（146.72 秒）**；额外独立审阅的 70 项恢复测试通过。
  各组重叠，不相加为总通过数。
- 主检查 Ruff、771 文件格式、519 文件主类型检查、Evaluation 独立类型检查、函数文档通过；
  最后恢复改动的 lint/format 与完整 recovery 70 文件严格类型检查通过。
- 原 main 的 Node CI 失败已修正并重跑：载荷限制仍未知，而已声明的楼层属性正常保留。
- 最终 wheel 的 22 个 workbench 与全部 54 个 Mission 模块逐字节匹配当前源码。
  隔离 Python / 三个目录启动、全局 Provider、显式项目选择、参数缩写拒绝均通过；
  wheel 默认 verifier 关闭、objective profile 为 echo。
- 实际 Engine/SQLite 的四个 host-profile/observer 组合探针确认：精确 execute 和独立
  风险审批之前不准备 observer；observer 拒绝时不产生 Controller POST。

## 实际进程门禁

所有过程只使用自有 loopback、临时证书、软件 Node、私有数据库与合成来源。
未调用付费模型、SSH 或真实设备，没有改变现场服务、原现场 SQL 或历史任务。

### 热接入、在线发布、取消和姿态观察

最终冻结 Controller/Node 重跑热接入与 online deployment：原会话/运行任务和绑定保留，
新身份通过原授权流程接入；冷重启、重复原 Mission 不重派发、错误版本与真实旧 writer
拒绝均通过。基础版本的首次过程结果与最终重跑分别保留。

取消结案的自动/人工两例各只发一次原生 Execute 和 abort，原 Mission 最终 Cancelled、
资源归零，冷重启与旧 writer 拒绝通过。这些软件结案不提供设备物理停止证明。

姿态 observer 门禁验证独立 mTLS verifier 权限、原 wire admission、两条同源新报告、
原十秒负证据窗口、Task Completed / SatisfactionRejected，以及重启不重复动作。

### v3 迁移与完整冷恢复

真实热接入过程产生的停机 SQL/WAL/SHM 在原 writer leases 下只复制到新目录；
之后所有操作均使用私有副本。原数据包含 99 条事件、1 个 checkpoint、42 条命令。

1. 真实 probe 对同一副本执行 legacy 读取、v3 迁移、重启，原事件/checkpoint/命令摘要一致。
2. 真实原 main Controller 对新格式拒绝启动，数据库字节未变。
3. 从未上传过内容的实际 legacy Artifact，仅有空 blobs/staging 和 writer lease，
   完成 v0.32 backup → verify → 新目录 restore，SQL 原表与 CAS 清单一致。
4. 实际迁移 Controller 经原 mTLS 入口上传/finalize 52 字节合成 Blob，再做完整 bundle
   与新目录恢复；所有原 SQL 表、Artifact 文件逐项一致。
5. 实际恢复 Controller `ready=true`，原完成 Mission、attempt、身份和零占用保持，
   原 Blob GET 字节相同。全部自有进程已回收，原输入 SQL/CAS 哈希不变。

实际 workload probe 另外写入并读回 10000 条原 State 事件（含 epoch 变化），
重复正文存为 1 个 body：4147 字节，对应 41470000 逻辑值字节。
这只验证固定重复值 workload，不代表现场混合数据压缩比或大历史性能承诺。

### 通用 Scene 的原字节与预算

实际 Scene HTTP 来源夹具 → 生产 Observer/Connector → 实际 mTLS Node → 实际
Controller State/SQL → 生产 MI reader 共 **15 项检查通过**。原浮点目标定义字符串及
摘要逐字节保持；实际发布的篡改/错实体 State 被拒绝，恢复来源后重新接纳。
原十五秒样本过期后，Node 外层继续发布也不能续鲜，World GET 不重新采集 Scene。
该过程没有 Mission、attempt 或资源 allocation，也不证明物理定位或到达。

前两次门禁的缺 capability 配置和错误租户读取范围失败均保留；仅修正门禁夹具，
未放宽生产来源、权限或时效检查。

## 验收边界与来源

默认仍是原存储选择、evaluation retention、verifier 关闭、echo objective。
v2/v3 必须显式 standalone 选择；复制入口拒绝未协商格式。
本次没有建立 24 小时稳定性、永久过期/全部压缩、replicated v3、物理停止或巡逻成功。
源分支中失败、中断、Unknown 和现场占用的原记录不因软件合并被改写。

本轮完整源码/二进制摘要、命令、测试、前后失败及远端核对：
`/tmp/rg-integration-plan-20261112-dhbioz6i/implementation/manifest.json`。

主要实际来源：

- 最终热接入：`/tmp/rgint-final-hot-20261112/result.json`。
- 最终在线发布：`/tmp/rgint-final-online-20261112/result.json`。
- 取消结案：`/tmp/rgint-final-settlement-20261112/`。
- 姿态 observer：`/tmp/rgint-final-posture-20261112/result.json`。
- v3 完整恢复：`/tmp/rgint-v3-cold-20261112/result.json` 与 `validation-manifest.json`。
- Scene 实际链：验收目录 `actual-scene-process-3/result.json`。
- wheel：验收目录 `wheel-acceptance-3nn77pnm/result.json`。

最终 native 二进制位于 `/data/workspace/code/roboguide-integration-artifacts-20261112/final/`，
真实原 main 兼容性程序单独保存在 `original/`；源码和选择性移植来源分别冻结。
