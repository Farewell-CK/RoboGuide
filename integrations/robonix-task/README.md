# Robonix 本机任务接入

姿态任务的“调用结束”与“目标达成”应分开。可选的
[reported-posture 目标评估器](robonix_task/posture_goal.py)复用既有 RG State 读取，
要求在确切本机 attempt 完成后记录 anchor，再等到同来源、同 epoch、原生采样标识更新的报告。
新的姿态不符得到 unsatisfied；离线、缓存重发、过期或未知得到 unknown，不自动重试动作。
读取时效同时受 RG 上限、Robonix stale_ms 与 Controller TTL 限制。

这只是纯评估器；生产调用方仍须认证读取、核对 AwaitingSatisfaction 与完整当前 attempt，
经已登记的 verifier source 向 /v1/verifier-evidence 发布，且扣除发布耗时。
此前现场 execution-report 任务不被事后改写。当前未部署姿态目标 producer，
不能将此文解释为所有现场任务已经自动启用目标验证。见
[ADR-0240](../../docs/decisions/0240-reported-posture-goal-profile.md)与
[无动作验收记录](../../docs/development/acceptance/2026-11-09-posture-goal-and-offline.md)。

维护的真实软件进程检查（所有状态均为 loopback fixture，不连接机器狗）：

```bash
PYTHONPATH=mission/src:integrations/robonix-task python3 \
  integrations/robonix-task/tests/posture_goal_process_gate.py \
  --controller /absolute/path/to/integration-server \
  --node /absolute/path/to/roboguide-node --output /new/private/ext4/evidence-directory
```

输出保留计划、State/inventory、原始 HTTP、验证回执、重启日志及二进制摘要。
WSL 下使用 Linux 文件系统保存运行中的 SQLite；进程全部退出后再复制验收证据到 Windows。

RG 的日常姿态检查使用 [reported posture 读取器](robonix_task/posture.py)：统一 Connector
启用 `--chassis-state` 后，固定采集 Robonix 的只读 `chassis/state`，复用已有 Node
`robot-state` 周期回传。中央只需 GET RG 的 State 和 inventory，无需每次创建 Pilot 任务。
读取器保留来源/会话/序号及原始数据，过期、未知或失联时拒绝给出当前姿态。
数据未提供单独姿态测量时间、阻尼模式或遥控器连接状态，不能据此推导这些信息。
该诊断读取器不自动为所有 Controller 任务增加姿态准入规则，也不授予运动权限。
见 [ADR-0239](../../docs/decisions/0239-reported-chassis-posture-state.md)。

后续真实 MI 起身重试已由用户确认站稳，RG State 也报告 standing。用户另行授权的
单次趴下仅出现原地踏步，RG State 仍 standing；原生停止/报告分支及 RG 的 Completed
不能当作趴下成功。见[姿态接入与动作重试验收](../../docs/development/acceptance/2026-11-09-dog2-posture-state-and-remote-retry.md)。

```bash
PYTHONPATH=integrations/robonix-task python3 -m robonix_task.posture \
  --controller-origin https://127.0.0.1:8080 --node-id lite3-dog2 \
  --ca /path/to/operator/ca.crt --cert /path/to/operator/admin.crt \
  --key /path/to/operator/admin.key --output /path/to/posture-evidence.json
```

任务级委派的 MI 目标须遵循 [ADR-0238](../../docs/decisions/0238-local-eaios-delegation-objective-boundary.md)：
RG 承担选机、身份/租户授权与审批。Pilot 只接收完整本机目标、实时前提和动作限制，
不被要求反查 RG 集群、证明自己是唯一候选或将本机名称匹配为 RG 部署别名。
适配器原样传递生成的目标；这些提示不替代 RG 的准入检查或 Robonix 的本机安全职责。

2026-11-09 已通过真实 `gpt-6.1-sol` MI 完成解释、规划、审阅与审批，再由原 Controller
派发狗2的一次站立实验。真实链路完成了失败回传；本机 stand 返回 standing/unknown，
实时仍 down，用户确认完全没有起身，RG 如实 Failed 并释放资源。
完整过程及保留的早期拒绝见[真实 MI 与站立实验](../../docs/development/acceptance/2026-11-09-dog2-real-mi-stand.md)。

2026-11-09 两台 Thor 已以 `lite3-dog1`、`lite3-dog2` 独立接入同一现场 Controller，
完成一个包含两个角色的正式只读任务。见[双主机接入验收](../../docs/development/acceptance/2026-11-09-two-host-enrollment.md)。
这次两台底盘尚未上电，不代表两只狗的运动能力已同时就绪。

随后狗2上电后，通过 RG 委派 Robonix 完成一次只读姿态/电量任务，原生只调用一次
chassis/state，返回 down、97%，RG Completed 并释放资源。
见[上电后只读验收](../../docs/development/acceptance/2026-11-09-dog2-poweron-readonly.md)。
该轮没有运动或 MI 规划；狗1后续排查已按用户要求停止。

[主机 Node 片段](deploy/host-node.fragment.toml)须使用 v0.14，并替换 `NODE_ID`；
现有统一 Connector 加 `--node-id`。固定 GET `/v1/host/status` 上报主机事实、
保留原机器人数据及其不可用状态，`system.host.inspect@v1` 通过正常任务链执行同一个只读查询。
[双角色 Mission 示例](deploy/host-mission-plan.json)使用 `independent` 模式和容量各为 1 的
专用 Compute class，使联合分配选择两个节点。未增加独立适配器进程，也没有调用 Pilot。
狗1首轮使用全新状态目录及独立证书，只声明主机检查；其 Pilot 委派须在本机服务就绪后另验。

起点接入的隔离实际进程与测试已通过，现场部署因 Thor2 离线待验。
边界及复现证据见[起点 Memory 与 MI 验收](../../docs/development/acceptance/2026-11-06-dog2-origin-memory-grounding.md)。

起点记录使用 [Node 配置片段](deploy/origin-node.fragment.toml)和
[无运动 Mission 示例](deploy/origin-mission-plan.json)：`spatial.origin.capture@v1`
只读取当前 Mapping 观察，不调用 Pilot。统一 Connector 显式配置 `--origin-root` 和
`--origin-node-id`，根目录须与 provider storage_directory 相同。只在原操作明确指定
`scope=global` 时向 Node 提供发现和导出。中央 Memory 发布仍由 Node 的真实会话、
provider 授权和 Artifact mTLS 完成；本地捕获成功与中央发布成功需要分别验收。
MI 的 `grounding_memory_content_schemas=["roboguide.reported-origin/v0.1"]` 才启用
有界内容读取，默认只读 metadata。原生运动任务仍走 Robonix Pilot，职责不变。
起点是历史报告位置，原测量时间、地图 revision、共享 frame 和定位质量仍未知。

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
真实运动取消和停止证明继续待验收；随后单独授权的
[公司门口导航到达](../../docs/development/acceptance/2026-11-05-dog2-company-entrance-navigation.md)
已完成并获现场确认。新片段声明 no-effects 支持，不声明设备停止能力。

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

2026-11-05 下午已在狗2实际应用上述两级结案，原历史保持、占用归零；随后一次正式
站立重试在 Robonix 模型阶段收到 API_KEY_DISABLED，零原生计划、零动作，已取消收尾。
当前结果及凭据恢复条件见[重试验收](../../docs/development/acceptance/2026-11-05-dog2-stand-retry.md)。

随后用户恢复凭据，RG 只读任务 Completed，站立任务因动作后姿态仍 down 而 Failed；
现场确认完全未起身。本轮原生任务明确 failed，正常回传并释放占用，不需要人工结案。
详见[模型恢复后验证](../../docs/development/acceptance/2026-11-05-dog2-model-restored-stand.md)。

15:07 用户再次授权后，新的站立任务经 RG 正常完成：一次 stand、动作后实时 standing、
原生 T1 done，RG Completed 并释放资源；独立只读复查仍 standing。本轮没有代码修改。
用户随后确认现场已站稳。
[成功记录](../../docs/development/acceptance/2026-11-05-dog2-stand-recheck-success.md)
另保留一次短暂 readiness 503 及尚待提供的具体现场原因，不覆盖前次失败。

## 从 RG 只读查询地图位置

`robonix_task.position` 只访问 Controller 的现有 State 和 inventory GET，不提交任务，
不直接查询 Robonix。统一 Connector 在原 robot-state 中补充 Mapping 缓存年龄；
旧缓存、节点不可达、外层过期、错误地图与无效坐标均显示 unavailable。
详情见 [ADR-0234](../../docs/decisions/0234-controller-reported-position-inspection.md)。

```bash
PYTHONPATH=integrations/robonix-task python -m robonix_task.position \
  --controller-origin https://CONTROLLER:8080 \
  --ca /deployment/ca.crt --cert /deployment/reader.crt --key /deployment/reader.key \
  --node-id NODE_ID --map-id EXPECTED_MAP_ID --output new-inspection.json
```

reported 表示来自明确来源且具有缓存年龄的 Mapping API 报告位置。它不证明原定位测量
时间、定位质量、地图 revision 或共享 frame，输出不授予运动、World grounding 或
MI 权限。`--output` 采用新文件，不覆盖原始证据；退出码 0 表示 reported，2 表示
unavailable。TLS/传输/格式错误单独失败，没有重试、代理、重定向或机器人回退查询。

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
