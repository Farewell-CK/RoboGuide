# Robonix 本机任务接入

本包在 Robonix 一侧将已有 Liaison/Pilot 任务协议转换为 RG Node 可配置的
execute/status/cancel 接口。任务由 RG 的正常 Mission、Matching、Commit 和 Bind
进入本机。Pilot 接收完整目标并决定本机步骤，Executor 执行本机能力。

2026-11-04 狗2的原地站立任务已完成一次真实闭环。任务
`dog2-delegate-stand-20261104T075534Z` 在 RG 中为 Completed，原生 Pilot 任务为 done。
本机执行记录依次包含 state、一次 stand 和 state，姿态从 down 变为 standing。
完整边界和早期失败记录见 [真机验收记录](../../docs/development/acceptance/2026-11-04-robonix-task-delegation.md)。

## 任务合同

部署可显式选用 [task.delegate@v1 示例目录](../../scenarios/local-task-delegation/capability-catalog.json)。
目标通过 `ExecutionIntent.objective` 原样传递，参数 `profile` 对应启动时配置的
`task-profile`。接收端匹配 profile 后将任务交给 Robonix 受理。

`local-task-v1` 表示当前人工监督的本机任务委派实验。它不实现按目标解析的权限过滤。
Pilot 使用现有 Atlas 能力和本机安全机制。需要严格技能白名单的部署应由 Robonix
实现并声明对应策略。本包不把 profile 字符串当作本机已执行权限隔离的证明。

默认公共目录保持现有定义。需要 MI 生成此类任务时，应显式选择含此操作的目录。
狗2使用确定的 MissionPlan，通过 ThinkPad 的 Controller 提交，当前选取依据是唯一
已注册此合同的节点和独占任务槽。多狗指定实体的流程须使用 RG 现有实体绑定机制。

## 运行

Python 3.12 或以上，依赖由 `requirements.txt` 固定。目录中的 `vendor` 保留原生协议
来源与 SHA256，运行时验证 descriptor 和 proto 的一致性。

在本目录启动单独的本机协议服务可使用以下命令。

```bash
python3 -m pip install -r requirements.txt
python3 -m robonix_task.adapter --journal /var/lib/robonix/rg-tasks \
  --atlas 127.0.0.1:50051 --task-profile local-task-v1 --enable-task-delegation
```

监听限制为 loopback，默认端口 18115。参数 `--enable-task-delegation` 显式开启派发。
Node 的固定连接配置指向本机服务。可用接口如下。

| 方法与路径 | 用途 |
| --- | --- |
| GET /v1/health | 本机协议进程健康 |
| GET /v1/readiness | 当前 Liaison 发现与本机任务槽可用性 |
| GET /v1/executions/safe-state | 原取消任务的 no-effects 证据，不能作为设备停止证明 |
| POST /v1/executions | 原 attempt 的完整 invocation 和执行预算 |
| POST /v1/executions/status | 按 handle 查询持久任务状态 |
| POST /v1/executions/find | 按原 attempt 找回本机 handle |
| POST /v1/executions/cancel | 对原会话请求取消并观察回执 |

`deploy/node.task.fragment.toml` 是狗2的配置片段，需与该设备原有 Node 配置组合。
`deploy/connector.py` 是现有狗2 Connector 的组合入口，复用现场已安装的 camera、artifact
和 observer 模块。它在同一进程中提供采图、状态和任务入口，Pilot 路径前缀为
`/v1/pilot/`。该组合入口依赖现场已有模块，独立任务服务可直接用上面的模块入口运行。

## 身份、结果与恢复

每个 RG attempt 对应一个独立的原生 session 和请求 task_id。原生会话内部的 T1/m1
与外部 ID 分别保存。同输入重传返回原 handle，输入冲突拒绝，新的 attempt 无法越过
未知任务的本机槽占用。派发边界前先持久化，重启后的未决记录保留 UNKNOWN。

正常结果同时要求原 turn_id、原会话、原 provider 记录、归属于 m1 的任务终态、完整
计划归属、每个计划根节点终态，以及最后的原生 FinalText 事件。Pilot 会在 FinalText
后关闭客户端流，因此后续 SessionStatus::Completed 不是这条流的完成条件。
FinalText 的文字内容单独不能产生成功。

原生 done 映射为 SUCCEEDED，failed 映射为 FAILED。RG Orchestration 再按任务声明的
execution-report 依据判定满足。姿态和硬件效果由 Robonix 确认，本包记录其结果。

取消受理保留原含义。现有原生 abort 的“正在取消”回复只使任务继续待对账。
原任务已确认 turn_id、没有产生任何计划且原任务流报告 interrupted 时，本机结果可归约
为 CANCELED。计划已经发出时，同一 interrupted 事件继续保留 UNKNOWN。
明确规划错误加原会话的“无活动 turn 或 plan”回复可以结束为 FAILED，但之前出现的
每项计划还必须有根节点成功证据。失败、取消、跳过或缺失的根结果均保留 UNKNOWN。
这种原生任务结果不替代 Controller 的独立安全状态证明。

[P0–P3 实测记录](../../docs/development/acceptance/2026-11-04-dog2-p0-p3-validation.md)
记录了一项接入缺口。当时 task.delegate 部署尚未声明 execution-safety 和对应状态导出，
因此取消后的本机 CANCELED 与 RG execution Cancelled 仍可能留下 Mission Cancelling
及资源占用。本分支按 [ADR-0232](../../docs/decisions/0232-task-cancellation-settlement.md)
补齐了无副作用取消和管理员结案的路径。2026-11-05 已部署到狗2及 ThinkPad，
旧任务正式结案、3 次原生规划期取消和一项只读任务通过，重启保持原结果。
详见[现场验收](../../docs/development/acceptance/2026-11-05-dog2-cancellation-field-validation.md)。
真实运动取消、停止证明及 P3 导航继续待验收。新片段声明 no-effects 支持，不声明设备停止能力。

safe-state 只报告明确未发送的原任务，或原 turn interrupted 且完整流中没有任何计划的
取消。缺 generation、未知、计划已发出、来源不匹配或缺终止事件时不报告。
RPC 最终发送检查明确阻止请求时记录取消，保留之前保守的派发标志和原身份。

管理员结案使用 Controller 的 `POST /v1/executions/{id}/safety-stop/reconcile`，请求格式见
[命令 schema](../../contracts/operator/stop-reconciliation-v0.1/command.schema.json)。
须有实际认证的 recovery-admin principal，身份从认证取得；仅设置 loopback 开发管理员
开关而没有可审计 subject 不能结案。原 Mission 必须已经显式进入 Cancelling。
单独取消 execution 不会隐式取消整个 Mission。结案保留原记录和 deadline，只供正常
Mission 取消释放使用，不解除恢复、续跑或物理交接的停止证据门槛。

`python3 -m robonix_task.reconcile DIRECTORY HANDLE --digest DIGEST --sequence SEQUENCE`
在服务停止后，仅对原始已保存的完整任务结果重新归约。它不发送原生请求。
源身份变化、缺计划结果或取消仍未结束时继续保留 UNKNOWN。旧任务的 deadline
继续由 Controller 判断，迟到的本机成功不能补写成按时完成。

原生进程重启后确实无法取得原任务终态时，管理员可按
[ADR-0233](../../docs/decisions/0233-local-administrator-cancel-settlement.md)在 Connector
停止期间使用离线工具：

```bash
python3 -m robonix_task.manual_settlement DIRECTORY --command REVIEWED_COMMAND.json
```

命令 schema 为 `robonix.local-cancel-settlement/v0.1`，必须提供原 handle、request_digest、
expected_sequence、expected_audit_sha256、command_id、confirmed=true、disposition=stopped、
attestation 和非空 evidence。完整摘要由 `manual_settlement.audit_digest` 在原 OS 锁下计算。
本机登录主体由工具取得。需先独立核验原任务停止和当前设备状态，不能用此命令制造
原生终态。工具只产生人工 CANCELED 与原回执，保留全部原生事件、旧状态及时间。
之后仍须 Controller 的正式管理员取消结案，不授予自动续跑；新任务另行授权。
新 journal profile v2 拒绝旧 writer，冷恢复逐项核对原始证据和人工回执。

## 检查

从仓库根目录执行以下命令。依赖按独立集成模块加载。

```bash
PYTHONPATH=integrations/robonix-task uv run \
  --with grpcio==1.71.0 --with protobuf==5.29.4 \
  python -m unittest discover -s integrations/robonix-task/tests -v
uv run ruff format --check integrations/robonix-task
uv run ruff check integrations/robonix-task
uv run python tools/quality/check_python_function_docs.py integrations/robonix-task
uv run --with grpcio==1.71.0 --with protobuf==5.29.4 \
  mypy --strict --ignore-missing-imports integrations/robonix-task/robonix_task
```

测试使用独立 loopback Atlas/Liaison、HTTP 和实际子进程，包括正常结果、身份冲突、
响应丢失、重启、取消、预算和事件容量。真实设备的结果另见验收记录。

静态类型检查覆盖生产协议模块。原生 gRPC 与 protobuf 动态描述符作为外部协议边界，
第三方类型存根可用性通过上述选项处理。

`tests/real_process_gate.py` 使用实际 Controller/Node 二进制、独立 Fake Atlas/Pilot、
持久 journal 和真实 mTLS 管理员验证自动/人工取消收尾、资源释放和冷重启。
显式传入 `--controller`、`--node`、`--output` 新目录及可选 `--old-controller`。
旧 Controller 必须在新结案 SQL 上以 unknown field settlement 拒绝启动且不改动原 SQL。
该检查不连接真实设备。所有派发只到夹具的 loopback gRPC，不能作为硬件验收。
