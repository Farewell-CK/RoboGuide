# RG 对话工作台 v0.4 实施计划

状态：已实现，最终验收记录见文末。承接 v0.3 的实际显示确认、原始命令回执、固定来源和证据完整性边界。

2026-11-12 的日常问答、活动任务统计和终端体验修正，见
[实际交互修正记录](conversation-workbench-interaction-2026-11-12.md)。

## 本轮范围

1. 安装启动：新增 `roboguide init` 与交互首次配置；自动读取用户全局配置，显式参数优先。
   独立对话模型配置不依赖源码仓库的 MI prompts/contracts。凭据由环境变量加载，不写入日志。
2. 状态栏：展示当前 Controller、身份、对话模型、会话、Request/Mission、前台处理与待确认项。
   普通终端提供 `/status`，异步终端显示固定状态栏；没有实际读取不声称服务在线。
3. 会话管理：`/sessions` 列出当前连接范围内的本地会话；`/new [名称]`、`/resume 名称` 切换。
   完成实际前台工作/跟踪排空后才释放旧锁；未确定命令禁止借新会话规避，旧回执完整保留。
4. `/check`：检查当前 Request 的精确计划版本，复用原 Controller validate-plan 与初始支持检查。
   仅展示本次检查的有效、阻塞与未知结果，既不提交 Mission，也不授权后续执行。
5. `/cancel request [ID]` 与 `/cancel mission [ID]`：先展示明确目标，`/cancel confirm` 后才发命令。
   放弃规划与取消执行分别走既有服务端入口。原操作先持久化；超时/丢响应不重发。
   取消请求已接收、Cancelling 与实际终态分别展示；软件终态不等于机器人物理停止证明。
6. `/feedback [目录]`：将当前会话日志、最近证据/错误和有限运行诊断脱敏打包到本地私有文件。
   输出包路径与清单；无外部发送，不扫描或打包任意服务目录、凭据、证书与私钥。

## 实施顺序

- 冻结 v0.3 源码/验收来源，并行实现全局配置、取消和本地反馈模块。
- 实现状态展示、会话切换与执行前检查，接入普通/异步/单次 CLI。
- 保持显示成功后采用选择、原始证据、会话来源/锁、pending 不重放边界；补充安装入口回归。
- 先跑各模块针对性测试，再跑工作台组合与受影响 Mission 包、严格类型/格式/文档和 wheel。
- 用本地实际 HTTP/SQLite、安装入口及伪终端验证；无需付费模型调用。保存失败和最终证据。

任务时间线、增强诊断/结果浏览、模型用量与远程反馈投递留待后续版本。

## 使用

安装后可运行 `roboguide init` 交互配置。配置保存到用户配置目录；只保存凭据环境变量名称，
实际 API Key 继续由该环境变量提供。启动前按现有部署运行 Controller 与 Mission Service。

```bash
roboguide init --base-url https://your-provider.example/v1 --model your-model
roboguide chat
```

`--base-url` 使用实际 Responses 兼容服务地址。默认拼接 `/responses`；高级模型配置可指定
`responses_path`。全局配置为 `$XDG_CONFIG_HOME/roboguide/config.toml`，未设置 XDG 时为
`~/.config/roboguide/config.toml`；独立模型在同目录 `model.toml`，会话默认位于
`$XDG_STATE_HOME/roboguide/sessions` 或 `~/.local/state/roboguide/sessions`。
显式 CLI 参数优先，然后是显式 `--profile` 和用户全局配置。当前目录中的项目配置不会
自动加载或改变模型凭据去向；项目配置须使用 `--profile config/workbench.toml`，
旧模型配置须使用 `--config config/mission.toml` 明确选择。
配置缺失的首次交互启动会引导初始化。传入的连接、租户、TLS、凭据环境变量和认证选择
会保留到生成配置；未显式提供的字段才询问。初始化发布失败时仅清理本次产生的精确文件，
保留原有或并发替换文件，允许正常重试。
初始化不覆盖现有配置；`roboguide doctor` 检查既有连接与模型配置，不调用模型。

```text
/status
/sessions
/new warehouse
/resume warehouse
/request REQUEST_ID
/check
/cancel request REQUEST_ID
/cancel confirm
/cancel mission MISSION_ID
/cancel confirm
/watch MISSION_ID
/observe
/feedback
```

`/cancel` 的目标需要明确 ID。取消规划请求与取消运行任务有不同来源和权限；只有成功展示
预览后才允许 confirm，确认时再次读取原对象。任务状态已变化时拒绝这次确认，需重新预览。
Controller 当前路径解析不支持精确编码的特殊取消 ID 时，客户端在任何效果前拒绝。
取消接收后显示原 `Cancelling` 或 `Cancelled`；继续用 `/mission ID`、`/watch ID` 查询。
丢失回包时 `/observe` 或 `/reconcile` 只做原操作对账，不重发；原 HTTP 未知状态保持。
若同一原 Mission/Group 已是 `Completed` 或 `Cancelled`，可独立解除本地待定操作阻塞，
并保留原 HTTP Unknown；`Completed` 不会被改写为取消成功。运行中、失败或身份冲突仍阻止。
会话切换先停止本地跟踪并等待已发读取完成，未确定命令会阻止切换。

`/feedback` 默认在当前会话目录的 `feedback/` 创建 ZIP；可用 `/feedback 私有目录` 指定位置。
包含 `session.json`、`conversation.log`（有历史时）、`observations.json`、`runtime.json`、
`README.txt` 和哈希清单 `manifest.json`。它打包当前工作台日志，**不收集远程 Controller、
Mission Service 或 Node 日志文件**。目录 0700、包 0600；单项、文本、结构和总包均有限额，
遗漏/截断明确记录。支持的认证格式和已知密钥会脱敏，任意未标记敏感文本不能保证自动识别。
JSON 字符串和日志中的嵌套/转义凭据同样处理；解析超出明确预算时标记遗漏，避免保留未经
清理的原始片段。脱敏只影响导出副本，不改写原会话与原命令回执。
反馈包只保存在本地；无邮件、上传或后台发送。

## 初版验收 · 2026-11-11（六项 review 修复前）

本轮新增首次配置 21、取消 20、反馈 11、前端组合 14 项回归，共 66 项。
工作台最终 **255 passed（40.52 秒）**；受影响 Mission 包 **882 passed（130.76 秒）**。
两组重叠，不相加。Mission 包运行后新增的两个会话切换显示失败用例包含在最终工作台组；
最后状态栏文案修正由前端组合专项再验证。strict mypy 136 文件、Ruff、137 文件格式、
函数文档与 diff-check 通过。未重跑全仓库 Python/Rust，也未调用付费模型。

实际 wheel 已离线构建，22 个工作台模块逐字节匹配源码；以 wheel 为唯一 RG 包来源，
在三个不同工作目录完成 init、chat/help、status，不依赖仓库 prompts/contracts。
实际伪终端验证 status、sessions、plan、feedback、new、resume、request 和 execute：
恢复后盲执行被拒，重新展示后恰好一次提交，本地反馈 ZIP 与日志清单可读。

实际运行取消验收使用原 Controller、一个真实软件 Node、原 OS 子进程与当前支持的
safe-state v0.3 配置。安装后的 CLI 仅发送一次 Controller cancel POST，先收到 Cancelling，
独立观察到原子进程退出 -15、原软件停止回执和 Mission Cancelled；没有产出结果文件。
这是软件进程停止证据，不代表真实设备或物理停止。
原旧配置缺少安全状态、以及初始化缺 verifier 的失败保持在各自证据目录，不覆盖为成功。

最终证据：`/tmp/rg-v04-final-2cwq1t3j/manifest.json`，内含源码摘要、回归日志、wheel、
伪终端与原生取消结果。原生完整来源：`/tmp/rg-v04-cancel-current-grpyaw2q/manifest.json`；
初始失败保留在 `/tmp/rg-v04-cancel-native-vumv9bcq/` 和 `/tmp/rg-v04-cancel-current-8ewn3q_k/`。
源码留在当前工作区，未发布或部署常驻服务。以上属于本轮 review 修复前的原始验收，
不替代下文修复后的来源和验证。

## 六项 review 修复验收 · 2026-11-11

六项问题均已修复，并新增 126 项回归：

1. **项目配置的隐式信任**：裸 `roboguide chat` 只读取用户全局配置；项目工作台和旧模型配置
   必须显式选择，不能仅因切换目录而改变继承凭据的接收地址。
2. **取消预览保留旧执行建议**：显式命令清除先前自然语言建议，`/choose` 保留其正常选择语义；
   会话导航及前端自行处理的跟踪命令同样失效旧选择。实际 F2 复现不再提交旧任务。
3. **反馈嵌套凭据泄露**：对支持的凭据字段递归处理 JSON、Unicode 和多层转义日志；
   带转义引号或未闭合引号的赋值/CLI 凭据完整清理，包括空格、末尾反斜杠与 CRLF。
   畸形编码或超预算内容明确遗漏；正常诊断保留，源会话字节不变。
4. **取消与正常完成竞争后的永久阻塞**：保留实际 409/HTTP Unknown，独立查询同一原
   Mission/Group 的 `Completed` 或 `Cancelled` 才解除本地阻塞。终态见证绑定原操作并冻结，
   重启后仍可核对，不重发 POST，也不把正常完成说成取消成功。
5. **初始化发布失败留下半套配置**：发布后的异常按实际文件身份回收本次新文件；
   若无法清理已发布的配置，则保留完整配置/模型对。四处同步失败注入均可正常重试，
   原有和并发替换文件受到保留。
6. **交互初始化忽略显式参数**：首次 chat 与交互 init 保留 Controller、Mission、租户、
   TLS、凭据环境变量和认证选项；init 不支持的对话专用参数明确拒绝。

最终工作台 **381 passed（55.66 秒）**，反馈专项 **81 passed（11.85 秒）**。
受影响 Mission 扩展回归 **1006 passed（153.35 秒）**，与工作台组重叠，不相加。
扩展回归在最后一处末尾反斜杠正则补丁前启动；该局部补丁后重新完成上述反馈专项及整个
工作台组。strict mypy 141 文件、Ruff、142 文件格式、函数文档与 diff-check 通过。
按影响范围验证，未重跑全仓库 Python/Rust 或调用付费模型。

最终 wheel 的 22 个模块与当前源码逐字节一致；以隔离 Python、wheel 为唯一 RG 包来源，
在三个不同目录完成 init、help 与 status。实际本地模型 HTTP 验证中，裸查询只访问全局
Provider，隐式项目请求为零，显式 `--profile` 恰好访问一次项目 Provider。
实际伪终端完成状态、会话、规划、反馈、切换、恢复、重新展示与一次提交；
原始取消快捷键复现提交数为零，完成竞争复现取消 POST 数仍为一。
反馈 ZIP 的最终凭据边界验证确认所有成员无测试哨兵、正常后续日志保留、源会话未改写。

完整来源、源码摘要、前后失败证据和测试日志：
`/tmp/rg-v04-six-remediation-xr5_kdmo/manifest.json`。
最终安装包来源为 `wheel-complete/`，其隔离验收为
`wheel-acceptance-4d2p4csx/result.json`。先前 wheel、失败复现与中间回归均保留，
不替代最终结果。修改保留在当前工作区。

## 合并前复核 · 2026-11-12

复核再次发现并补齐两处边界：CLI 现在只接受完整长参数名，拒绝 `--cont`、`--ten`、
`--conf` 等缩写，避免已接受的显式连接/身份参数被全局配置覆盖；新 Request v0.5 的
公开 schema 同时声明运行时已支持的 MissionPlan v0.10/v0.11，保证实际仅规划记录可按
公开合同校验。原有 Request v0.4 合同与执行授权保持原意。

本次集成包括当前 dev 的六个既有提交和上述工作台实现；远端其他功能分支单独列出
合并状态与包含关系。使用隔离 main 工作树完成合并检查，现有本地实验与其他未提交内容
继续保留。完整来源及合并后的远端提交核对见
`/tmp/rg-chat-main-integration-ftmgyb60/manifest.json`。

获取远端后发现其他功能分支已使用 ADR-0231～0234；本轮四份新决策在发布前统一调整为
ADR-0251～0254，并更新中英文架构表和使用文档引用。编号选择核对了所有已获取分支，
此调整只涉及文档，保留各功能分支的原始决策编号。
