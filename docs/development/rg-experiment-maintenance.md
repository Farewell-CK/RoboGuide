# RoboGuide 真机实验问题与分支维护

本会话按用户授权维护实验暴露的 RG 问题、修复验证与分支整合。记录按实际源码、
提交、进程和原始回执更新；第三方诊断是待核验输入，不能直接当成已复现的结论。
本页首次清点日期为 2026-11-09，源码基线 c7615774；清点不运行机器人任务或变更现场服务。

## 当前清点结论

ThinkPad 的 `/home/duchaoke/workspace/RoboGuide` 只有本地 `main`，HEAD 为
1a62bf9113b02633f446adf0b0a1d0d55fbc8982。无未提交/暂存改动、无 stash、无额外 worktree，
相对已保存的 origin/main 为 ahead=0、behind=0。检查了 `~/workspace` 与 `~/rg-lab`
中深度不超过 5 的 `.git` 标识，只有这个仓库。Git 返回的 origin/codex/* 是远端跟踪引用，
不是额外的本地开发分支。

ThinkPad 当时访问 GitHub 失败，明确错误为 github.com 临时 DNS 解析失败。因此其自身
origin/* 不能独立证明远端当前状态。随后从 Windows 直接 `git ls-remote --heads origin`
核验 main 与下面四个实验分支，远端提交与本次本地清点一致。没有 fetch、pull、reset、
checkout、合并或删除 ThinkPad 分支。

现场修复使用 `~/rg-lab` 的独立部署目录；这些目录多数是复制/归档部署源，不是本地
Git 分支。不能据 `~/workspace/RoboGuide` 干净就认定现场没有修复，也不能把部署目录
当作尚未推送的 Git 提交。

## Windows 本地分支与合并关系

以下提交数冻结于审计基线 c7615774，均相对 main=1a62bf91；后续维护记录提交会增加计数。
四个分支的本地 HEAD 与本次直接查询到的 GitHub HEAD 相同，没有未推送提交。

| 分支 | HEAD | main 尚未包含的提交数 | 关系/处理 |
| --- | --- | --- | --- |
| codex/dog2-origin-memory-grounding | c7615774 | 30 | 当前主实验修复线，116 个文件变更；需审阅和整合验收后再进入 main |
| codex/dog2-spatial-state-ingress | 8da0ca8b | 21 | 当前主实验线的祖先，已被完整包含 |
| codex/task-cancellation-reconciliation | be65b9df | 18 | 空间接入线的祖先，已被完整包含 |
| codex/local-eaios-task-delegation | 89dca456 | 9 | 与主实验线分叉；有 5 个未被主实验线包含的手机方案/审阅文档提交 |
| main | 1a62bf91 | 0 | 与 ThinkPad main 和直接查询到的 GitHub main 相同 |

已核验的祖先链是：

```text
main → 任务委派公共基础 → 取消对账 → State 位置 → 起点 Memory / MI
                     └→ 手机方案/审阅文档（另 5 个提交）
```

后三条实验修复分支不能当作三套独立补丁重复 cherry-pick。委派分支的另 5 个文档提交
需要独立审阅；`89dca456` 并非整条起点/MI 分支的祖先。不要把所有分支当作一条线直接删除。

主工作区 `C:/workspace/DEAIOS/RoboGuide` 当前位于委派分支，还有未提交的手机 Node 验证
改动：Cargo.lock、apps/real-node-smoke/Cargo.toml，以及新增
apps/real-node-smoke/src/bin/verify_phone_node.rs。前两项加入 sha2 依赖，新增源码为
loopback Phone Node 软件探针；本次只读取，没有运行、提交、移动、覆盖或清理。
本会话的工作区 `C:/workspace/DEAIOS/rg-task-cancellation-reconciliation` 在清点时干净。

## 现场部署与版本归属

| 项目 | ThinkPad 实际路径/版本 | 本次核验 |
| --- | --- | --- |
| Controller | ~/rg-lab/cancellation-20261105/bin/integration-server.release | 服务 active；SHA256 为 7a01be0085643706da2eed7b48b2955d092c42a43fd75a735205eaf1d684a403，仍为此前验收的 release |
| MI | ~/rg-lab/dog2-mi-posture-20261109/source | 服务 active；四份 Interpreter/Planner/Reviewer/Repairer 提示及委派 catalog 摘要与当前实验分支一致 |
| 主 Git checkout | ~/workspace/RoboGuide | main 的旧基线，不是上述现场服务的工作目录 |

部署、版本合并、软件测试与机器人效果分别记录。之前的站立实验已经跑过真实 MI、
RG 与 Robonix，但物理站立失败；完整记录见
[真实 MI 与站立验收](acceptance/2026-11-09-dog2-real-mi-stand.md)。
不要把它写成 MI 未运行，也不要把其任务链路验收写成物理站立成功。

## 新节点热接入问题台账

### RG-HOT-001：静态 Node mTLS 到生命周期 registry 缺少显式在线采用入口

状态：已在 `codex/rg-hot-enrollment-maintenance` 修复并完成隔离软件进程验收，未部署现场。
详见[验收记录](hot-node-enrollment-acceptance-20261109.md)。此前现场通过完整备份、副本软件验证后更改启动
profile 并重启 Controller 才解决 MI 的租户接入，属于部署处理，不是此缺口已修复。

证据：

- `core/integration/src/grpc_server/mod.rs:61` 将静态字典与生命周期 JSON 分别解析；
  修复前 publish_identity_registry 在当前 profile 不是 CertificateLifecycle 时拒绝。
- `core/control/src/node_identity.rs:52` 的 apply_node_identity_command 在 registry=None
  时拒绝。application/node_identity::submit 持久化后热发布的正常链路已经存在。
- 完整生命周期部署下，register 可热更新；不能概括成 RG 完全不支持节点热加入。

修复范围应是认证管理员显式采用/初始化，不应从裸证书摘要静默猜测 tenant/domain。
必须保持现有叶绑定、历史 Mission 租户和 actor ownership，先验证并提交、后发布；
检查失败/SQL 失败/重复命令/并发命令/冷恢复/旧 profile 降级和实际存量会话行为。
新增注册表不能伪造新节点的能力、会话或硬件可信证明。

### RG-HOT-002：PhysicalEntityRegistry 缺少运行期管理接线

状态：底层更新能力和 HTTP 接线缺口已确认，框架修复待做。

`core/control/src/lib.rs:1578` 已检查 provenance 单调性及现有 actor_bindings 冲突，
但 `apps/integration-server/src/main.rs:1100,1552` 仅在启动安装当前 snapshot；
现有 Controller HTTP 路由没有 `/v1/physical-entities` 管理入口。

这是显式 PhysicalEntity 绑定/放置场景的缺口，不代表不绑定实体的普通 Node 无法执行。
底层代码还明确要求当前物理 registry 不由 Control checkpoint 直接恢复：checkpoint
保留的是绑定时 provenance。补 HTTP 不能只对 live 内存赋值；需定义认证管理输入、
原 revision/digest、提交/恢复来源、保留历史绑定、并发写与降级规则。
验收需要新增实体热更新后的真实匹配/Commit/Bind，并拒绝冲突和旧快照回退。

### RG-HOT-003：启用 DeploymentFeasibility 的部署缺少受控在线版本切换

状态：启动冻结及缺少在线入口已确认；受影响部署范围已限定，框架修复待做。

`main.rs:1104,1220` 从可选环境路径读取一次并包为 Arc；HTTP 持有同一只读对象。
`application/plan_revision/checkpoint.rs:395` 还要求恢复后的运行期 revision 继续绑定
原 deployment digest。它包含原初始世界、操作准入、端点和证据语义，不是 live inventory。

所以该缺口不能概括为“所有新节点热接入都被阻断”，也不能通过简单文件监视/替换 Arc
把旧候选、原证据年龄和旧计划的权威切到新版本。修复需明确版本所有权：新请求采用
新版本，原候选/运行期 revision 保留原声明；还需与实体版本、World identity、时效和
持久恢复协调。实际现场本轮的 MI 503 是 RG-HOT-001 的部署缺口，不能归因于本项。

## 已修复与本地合并的后续处理

1. 保留上面的原始版本、部署源摘要、未提交手机代码与实验失败记录。
2. 用独立整合工作区审阅 30 个已推送实验提交及另 5 个文档提交；沿祖先链整合，
   不在有未提交手机工作的主工作区 checkout、reset 或 merge。
3. 先处理 RG-HOT-001，再处理实体 registry 管理，最后处理可行性证据版本切换；
   每项先明确合同/ADR、验证权限和恢复边界，再做实现与实际软件进程验收。
4. 通过整合验收后再合并主线。现场升级独立执行，不因 Git 合并自动重启服务，
   不触发任一机器狗动作。无用户后续授权不排查或修改狗1。

本轮清点没有合并/删除分支，没有运行机器人任务，没有修改 ThinkPad Git 工作区、
服务、私钥、模型凭据或数据库。公开证据在工作区
`lab/rg-maintenance-audit-20261109/`，包括 thinkpad-git-inventory.json、
thinkpad-deployment-inventory.json、thinkpad-remote-heads.json、windows-git-inventory.json、
github-heads-verified-from-windows.txt 和 hot-enrollment-source-evidence.json。
