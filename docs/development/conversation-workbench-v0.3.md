# RG 对话工作台 v0.3

## 使用

```bash
uv sync --dev --frozen
uv run roboguide chat --profile config/workbench.example.toml --session warehouse
uv run roboguide chat --profile config/workbench.example.toml --resume warehouse
uv run roboguide doctor --profile config/workbench.example.toml
```

交互终端默认异步输入，非 TTY 自动使用普通模式；`--plain` 可显式选择普通模式。
`--answer-mode grounded` 使用工具证据解释，`--answer-mode facts` 保留原单查询路由。
直接 `/nodes` 等命令仍无需模型。`doctor` 只读取两个服务的 readiness，并检查模型配置与
凭据是否存在，不自动启动服务、调用模型或放宽 TLS 策略。

自然语言可以连续查询与解释；每轮最多三次模型调用、四次底层 HTTP 读取、60 秒。
`--call-limit`、`--tool-limit`、`--turn-seconds` 可降低这些上限，profile 使用对应下划线字段。
解释标记为模型解释，并引用本轮 `[E1]` 等证据；程序生成的事实独立展示。模型解释并非
新的系统权威，引用存在也不证明所有自然语言推理正确。没有足够证据时保留事实与缺口。

最近 10 轮对话在 24 KiB 预算内参与理解；旧文本明确标为历史。单份投影超过 24 KiB 时
不送入模型拼凑统计，提示缩小查询。模型不接触凭据或未授权 endpoint。

同一批模型查询先固定序号对应的实际 ID 和比较的节点集合，再逐项读取；中间的列表
刷新不会改写这些对象。下一次模型调用可以依据新返回的列表继续查询。
`/explain` 独立保留 Mission、Group 绑定和原恢复评估的可用性；任一读取失败、格式无效或
截断，组合证据均为不完整，不能被引用为完整解释依据，已取得的事实仍展示。
`chat --ask` 只有完整回答或成功的直接命令返回 0；部分结果、无有效证据或解释失败返回 2。
真实的空库存仍是成功，不会与 HTTP 拒绝或服务不可用混淆。

## 终端与会话

- `/watch ID` 后可以继续输入；`/watches` 查看活动跟踪，`/unwatch ID` 停止观察。
- 最多三个后台跟踪，每个最多 150 次，每次间隔两秒，仅状态变化时通知。
- 单个前台请求处理时仍可编辑输入或查看/停止跟踪；新前台请求不自动排队。
- 操作建议支持 F1 选择仅规划或所示单项动作、F2 选择开始执行流程；没有对应提议时按键无效果。
- Ctrl-C 停止等待显示或清空输入，原已发送操作继续完成并保存回执；退出时等待实际工作收尾。
- 被隐藏、未完整输出或 flush 失败的草案和操作建议不能继续确认；用 `/request` 重新展示草案。
  F1/F2 只在对应建议完整展示且前台空闲时生效。
- 普通模式与异步模式使用同一显示确认边界。查询输出成功前，持久化引用仍指向上一份
  已展示列表；隐藏或失败输出恢复这些引用，原 Request、命令及回执独立保留。
- 计划显示目标、步骤、原参数和版本变化；`/plan-detail` 展开完整计划。执行仍绑定原版本摘要。
- 恢复 v0.2 会话时新建 `<原名>-v03` 副本，原文件不改写；再次恢复使用新名字。
- 新会话名仍限制为 80 字符；`--resume` 额外接受由合法旧名称加 `-v03` 生成的 v0.3 名称，
  因此最大长度旧会话及已经生成的 81–84 字符副本也可恢复，无需改名。
- 原 pending 命令不能被升级、历史裁剪或模型解释清除；仍通过 `/observe`、`/reconcile` 对账。

`prompt_toolkit` 接线参考[官方异步输入与输出文档](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_input.html)。
不增加任务取消、直接机器人命令、自动计划修改或跨域聚合。

每次 HTTP 请求的连接、TLS、发送、状态行、响应头、chunk framing 和正文读取共用原始
单调时钟期限；收到少量数据不会延长期限，超时不会重发 POST。DNS 解析仍受主机 resolver
约束，不构成硬实时保证。

响应拒绝冲突的长度声明、含糊的传输编码，以及缺少正确 chunk 分隔符或终止符的正文。
首个解析地址的 socket 无法创建时，后续地址仍使用同一原始预算尝试。模型查询中的具体
Mission ID 直接进入字面 ID 读取路径；只有用户命令入口将 `#N` 解释为列表序号。

## 验证

第二版基线保存于 `/tmp/rg-v03-baseline-2ufxqlqq/`，包含源码摘要、原文件和 tracked diff。
实际通过链路：`/tmp/rg-v03-live-s5j6tl0k/result.json`。使用用户指定的 Provider 和
`gpt-6.1-sol`，真实 Controller、Mission HTTP/SQLite、两个独立软件 Node 和文件计算进程。
查询到两节点不同能力、比较、模糊确认澄清、真实 MI 仅规划、修改到第 2 版、恢复会话、
明确 execute、独立文件 SHA-256 verifier、Mission Completed 全部实际发生。
仅规划和修改期间没有 Controller Mission 或计算进程；最终只有一次文件计算。
停止第二个软件 Node 后，模型依据新读取回答 Unreachable，同时保留旧健康报告 Online 的区别。

累计 41 次实际 Provider 请求（上限 60），40 次有返回用量记录；输入 147695、输出 8002、
合计 155697 token。一次失败调用没有返回 usage，故这些是**已返回用量之和**，不是账单总额。
用量汇总：`/tmp/rg-v03-model-usage.json`。原始响应确认模型为 gpt-6.1-sol，未设置输出 token 上限。
HTTP 地址仅在本次明确授权的隔离验收配置中使用；凭据不写入仓库和结果包。

原失败保持独立：

| 原目录（均在 /tmp） | 原结果 |
| --- | --- |
| rg-v03-live-ofgql1uc | 策略安装缺少本地管理权限，0 次模型调用 |
| rg-v03-live-cplxldza | MI 超过原 HTTP 连接期限，原请求 pending，不重发 |
| rg-v03-live-gaydny_9 | Reviewer 拒绝 10000 ms 用户要求与 5000 ms 原策略冲突 |
| rg-v03-live-yz55plnb | 模型空查询参数被拒；后续软件计算完成，但验收 verifier 有效期字段未对齐而被拒 |
| rg-v03-live-214a8zqy | 模型结构化引用存在、正文无重复标注，被过严检查拒绝；随后改为程序呈现验证后的引用 |

实际终端证据 `/tmp/rg-v03-terminal-jbmv4bg_/`：伪终端运行安装入口，帮助、跟踪列表、Ctrl-C、
退出通过，exit=0。软件节点和模型事实不证明真实设备执行或物理停止。

本轮 review 修复前，相关回归 728 passed（92.80 秒）；工作台专项 76 passed（10.04 秒），日志
`/tmp/rg-v03-final-targeted.log`。完整 Python 回归 **3725 passed（580.97 秒）**，日志
`/tmp/rg-v03-full-python.log`。各组重叠，不相加。完整回归之后补充的两项 watcher/migration
专项也已单独通过。这些是修复前的基线结果，本轮修复另行运行专项与受影响包回归。
严格 mypy 115 个文件通过，Ruff 和函数文档检查通过。依赖安装通过 frozen/offline 检查。
离线 wheel 构建成功并核对 13 个工作台 package 文件；原构建在 `/tmp/rg-v03-package/`。
没有重跑 Rust 全套，没有推送、部署或发布；所用实际 Rust 二进制摘要保存在 live result 中。

### 2026-11-10 review 修复验收

修复隐藏草案仍可确认、同批查询引用变化、诊断缺口误标完整、HTTP 总期限失效、
自然语言 CLI 失败返回成功五项问题。比较集合及比较后的继续追问也保持实际显示顺序。
新增 28 项回归；最终工作台专项 **104 passed（21.29 秒）**，受影响 Mission 包
**733 passed（104.45 秒）**，两组重叠。strict mypy 120 文件、Ruff、121 文件格式及
函数文档检查通过。未重跑全仓库或 Rust 测试。

原问题复现重新验证：隐藏草案后 Controller 提交数为 0；两个序号分别读取 a/b；缺失恢复
评估时组合证据不完整且拒绝模型解释；原 0.1 秒 HTTP 期限实测约 0.101 秒；HTTP 403
自然语言查询 exit=2。实际伪终端通过安装入口显示草案、明确执行、退出，执行前提交 0 次，
执行后恰好 1 次。这里使用真实本地 HTTP/SQLite 与确定性模型桩，不增加真实 Provider 调用。

证据目录：`/tmp/rg-v03-five-fixes-_l_eliui/`；`manifest.json` 记录当前源码摘要与来源，
`results.json` 保留问题复现结果，`result.json`/`pty.log` 记录实际伪终端，
`workbench-tests-final.log`/`mission-tests-final.log` 为最终回归日志。
首次整包回归的 731 pass / 1 fail 保存在 `mission-tests-initial.log`；该失败来自原 30 ms
超时测试立即断言 handler 已调度。现改用实际响应体门控并等待 handler 清理，目标测试另行
重复 10 次通过；原首次失败证据保留。

### 2026-11-10 第二轮 review 修复验收

修复普通终端未展示草案仍可执行、隐藏查询改写引用、字面 Mission ID 被重解析、
畸形 HTTP 分帧被接受、长迁移名不可恢复、首个地址族失败后不回退六项问题。
普通与异步终端共享显示确认；处理期间保存旧可见引用，只有完整输出、flush 和
checkpoint 成功后才采用新引用和确认选择。引用回滚也无法保存时，暂停后续命令并返回 2。
原请求、实际操作和回执不回滚；新会话名称上限保持 80 字符。

新增 **85 项回归**：显示 18、字面 ID 19、分帧及回退 38、迁移名称 10。
最终工作台专项 **189 passed（41.17 秒）**，Mission 包 **818 passed（130.07 秒）**，
两组重叠。strict mypy 125 文件、Ruff、126 文件格式、函数文档和 diff-check 通过。
离线 wheel 构建通过，包内 15 个工作台源码模块逐字节匹配当前实现，包含新增显示模块。
可选 sdist 构建在打包完整工作区时主动停止，未作为验收项；wheel 独立完成。
未重跑全仓库或 Rust 测试。

实际复现确认：普通模式写入或 checkpoint 失败后盲目 execute 的提交数均为 0；隐藏
`[b,a]` 刷新后，`#2` 仍指向原已展示列表中的 b；字面 `#1` 的实际 HTTP 路径为 `%231`；
原三种畸形库存响应均返回 exit=2；80 字符旧会话迁移成 84 字符副本后正常恢复，
原文件与 pending 回执保持；首个 IPv6 socket 创建失败后实际 IPv4 读取成功。
实际伪终端显示草案、明确执行、退出通过，执行前 0 次提交、执行后恰好 1 次。
这些验收使用本地 HTTP/SQLite 和确定性模型桩，未增加真实 Provider 调用。

证据目录：`/tmp/rg-v03-six-fixes-vk9f7o5s/`。`manifest.json` 记录当前源码摘要和复现来源；
`workbench-tests-final.log`、`mission-tests-final.log` 保存最终回归，`network-acceptance.json`、
`migration-result.json`、`pty-result.json`、`wheel-verification.json` 保存对应实际验收结果。
