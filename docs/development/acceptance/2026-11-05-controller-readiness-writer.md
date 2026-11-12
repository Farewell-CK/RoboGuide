# 2026-11-05 Controller readiness 与原应用写入串行化

狗2站立复测曾观察到一次 `/readyz` 503，随后自行恢复。原回执保留在
[站立验收](2026-11-05-dog2-stand-recheck-success.md)，该次没有捕获底层错误。

## 已复现的问题与修复

新增隔离回归使用实际生产 HTTP handler 和同一 ApplicationWriter。一个真实 writer
已开启 SQL batch 并写入候选 checkpoint，但尚未提交时，旧 handler 直接调用
EventLog readiness，得到“event batch is still open”，返回 503。原 writer 随后可以正常
提交。冻结旧实现的反例已失败，响应与现场相同，但不能由此断定历史单次 503 的唯一原因。

修复让 readiness 在原 ApplicationWriter 的普通 FIFO turn 内检查原 EventLog 与当前
unknown 计数，正常 batch 先完成；同步等待沿用现有 blocking handoff。readiness 不获得
业务写入权限，不创建命令、资源或新预算。原认证和角色检查、SQL/副本/epoch、checkpoint
及写失败隔离均保留。实际存储权威关闭时仍为 503，且增加有界的内部错误诊断。

没有修改 checkpoint、Node、停止证据格式或物理执行职责。修复位于现有 HTTP handler，
不改变 EventLog 对未提交数据的读取拒绝。

## 已执行的软件检查

- 新增的真实批次反例在旧 handler 上返回 503；修复后等待原提交并返回 200。
- 实际 disabled-write fence 的反例继续返回 503，原失败记录保持。
- Controller 316 项、State 127 项测试全部通过，共 443 项。
- workspace 全 targets、全 features 的 Clippy 通过，Rust 格式检查通过。
- 同一冻结实现的 release 构建通过。实际 Controller/Node 的自动取消、管理员结案和
  本机离线管理员结案三项软件进程门禁通过，均保持一次原生受理、一次 scoped abort、
  冷恢复及旧 writer 拒绝；全部只连接隔离夹具，不作为硬件停止证据。
- 现场部署与历史保持结果见下节，软件检查不替代部署或硬件验收。

首轮修复后测试已得到 200，但用例错误地把原 writer 保存 checkpoint 的命令计为探针
新增命令。计数现以原批次实际提交后的基线比较，完整测试重跑通过。
旧失败、修复后结果、完整测试及构建日志均保存在工作区
`lab/rg-p1-validation-20261105/`。源码在独立专题分支维护。

## ThinkPad 实际部署与保持

现场 Controller 已升级到 `2d6a043c7f0574fa0d7ca5f2f28ccc4bab5fcdb0` 的 release，
二进制 SHA256 为 `7a01be0085643706da2eed7b48b2955d092c42a43fd75a735205eaf1d684a403`。
原服务和持久库保持，停服备份后冷启动至 ready 用时 55.115 秒；服务仍为 enabled。

升级前后选择的 9 项 Mission、9 项 attempt 视图及 2 项原 stop 视图逐项完全一致。
狗2原生 journal 的 20 条 runs、284 项 events、2 项管理员回执和 meta 表摘要保持，
未重发本机任务。资源占用和 Unknown 均为零。

升级后的只读导航预检期间，12 次实际 `/readyz` 探针均返回 200，最大延迟 27.662 ms。
这是本轮有限窗口的结果，不作为长期可用性或时延承诺。随后同一部署通过 RG 完成了
[公司门口导航](2026-11-05-dog2-company-entrance-navigation.md)。
