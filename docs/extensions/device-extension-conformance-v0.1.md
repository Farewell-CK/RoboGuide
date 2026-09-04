# Device Extension Conformance v0.1

本合同把“接入一个新设备或 Local EAIOS”收敛为节点部署配置和受支持的通用
driver。正式链路保持：

```text
ExecutionIntent
  -> Node Protocol v0.3
  -> roboguide-node
  -> Local Integration Engine
  -> configured HTTP / dynamic gRPC / MCP
  -> deployment-owned Local EAIOS facade
```

## 责任边界

| 位置 | 正式责任 | 明确不负责 |
| --- | --- | --- |
| `core/integration` | Node Protocol v0.3 protobuf wire、gRPC session、lease/session fencing、NodeId command router 和 wire 校验 | Control reservation/recovery、Runtime/Task lifecycle、Local How |
| `core/orchestration` | Controller application composition，包括 `IntegrationRuntimeBridge` 对 Control/State/Runtime 的事实归约 | transport framing、endpoint 选择、Local EAIOS 调用 |
| `core/artifact-store` | 独立 filesystem CAS、分块上传和 digest/path 安全 | Map/Task/Group 状态、Control ownership、Local EAIOS workflow |
| `core/node-service` | 每台机器唯一的 `roboguide-node`、配置编译、HTTP/dynamic gRPC/MCP driver、journal、local lock 和 status/cancel lifecycle | 物理 safety、Immediate How、Control commitment/recovery |
| `integrations/` | deployment-owned facade 的 vendor SDK、ROS/EAIOS mapping、Local Safety 和 Immediate How | 修改 RoboGuide core、选择 Node/Resource、Mission/Group/Task authority |

`IntegrationRuntimeBridge` 因为依赖 Control、State 和 Runtime，属于 Controller 组合层，
不再位于 `core/integration` transport crate。迁移不改变 Node Protocol wire 合同或
checkpoint schema；`core/orchestration` 只把 transport fact 接到既有 authority。

## 离线 conformance

`roboguide-node` 的配置编译器先整体校验并冻结配置，再允许进程连接任何 endpoint。版本
`roboguide.node-config/v0.6` 是 Extension Conformance 的当前版本；v0.5 仍可作为
metadata-only Memory provider 配置启动，v0.2-v0.4 仍可解析为
空 State/Memory declaration，但不满足当前 conformance。Node Protocol v0.2 endpoint 只返回
明确迁移诊断，不再接受 session。

成功编译只证明以下静态部署不变量：

- 每个 canonical `namespace.name@version` 只有一个 local-system owner；
- v0.5/v0.6 每个 exact capability 都有独立 readiness probe，未知值、超时或 probe failure
  fail-closed 为 unavailable；
- 每个 State export 固定 owner、Node/World object、Reported/Observed semantic、schema、TTL、
  sampling interval 和固定 observation workflow；每个 Memory provider 固定 owner、五类 kind、scope、
  visibility、schema 和 media type；v0.6 可再声明固定 discover/export/import workflow。`discover`
  workflow 返回的必须是 provider 已授权 RoboGuide 发布的 publish-eligible manifest 集合，不能
  把 Local EAIOS 的全部 Memory 暴露给 Node Service；Node 只执行 publication mechanism；
- Memory conformance 为 v0.1 兼容保留 `local_backend` 字段，但它只表示 Node manifest
  ledger/reference fallback 可用，不表示真实 EAIOS authority；独立 workflow flags 表示 EAIOS
  operation routes，`shared_data_plane` 表示 Catalog/Artifact exchange；
- connection endpoint、HTTP method/path、gRPC service/method/descriptor 或 MCP tool
  由配置固定，网络 `ExecutionIntent` 不能改写；
- execute、status、cancel 都是非空、有序 workflow，local handle 只能来自 execute
  response，execution state 映射不允许同一值映射多个 phase；
- request mapping 只能使用 JSON Pointer、常量和白名单转换函数，不能执行 shell 或
  远程传入 executable；
- required resources 必须存在、容量非零且属于 capability owner；local lock 只保护本机，
  不创建或撤销 Control reservation。

这些检查不执行 HTTP、gRPC、MCP 请求，也不访问远端 Controller。诊断包含
`connection`、`capabilities.<contract>`、`workflow` 和 `workflow.step.<id>` 路径，且报告不
包含 credential 值。报告显式输出 `runtime_probes_executed=false` 和
`hardware_probes_executed=false`。

State observation route 固定不等于离线编译能证明外部操作无副作用。deployment-owned facade
必须把该 endpoint/method/service/tool 实现为 observation；这一点与真实 health/readiness 一样
需要运行时或硬件验证。

报告的 `implementation_guarantees` 单独列出当前 Node Service 代码由 engine/journal 测试覆盖的
不变量，例如 status 才能确认 terminal outcome、cancel acknowledgement 不合成 `Cancelled`、
timeout/unknown fencing、identity conflict 和 restart no-replay。它们不是当前配置、facade 或
物理设备已经通过动态认证的结果。v0.1 为现有机器消费者保留内容相同的 `lifecycle` 兼容字段；
该字段同样不表示执行过 runtime 或 hardware probe。

## 开发者路径

1. 在设备旁边提供 deployment-owned facade。它负责 Local Safety 和 vendor-specific
   Immediate How，并暴露固定的 health、readiness、execute、status、cancel 操作。不要把
   executable、endpoint、service、method 或 tool 放进网络 intent。
2. 复制并修改真实配置样例
   [`scenarios/extension-conformance-v0.1/node.toml`](../../scenarios/extension-conformance-v0.1/node.toml)。
   该样例在同一个 Node 中声明 HTTP、dynamic gRPC reflection 和 MCP 三种 connection，
   每个 capability 都有 readiness、required resource 和 execute/status/cancel workflow；样例还
   声明 State export 以及 Execution/Spatial/Semantic/Experience/Artifact 五类 Memory provider。
3. 在不启动 facade、不启动 Controller 的情况下运行：

   ```bash
   cargo run -p roboguide-node -- --validate \
     scenarios/extension-conformance-v0.1/node.toml
   ```

   命令输出 `roboguide.extension-conformance/v0.1` JSON。失败诊断会删除 TOML 原始 source
   line，避免 credential 值进入终端或 CI 日志。按诊断路径修正配置，
   例如 `connections.demo-grpc.descriptor_set`、
   `capabilities.demo.http_action@v1.readiness` 或 `workflow.step.http-status`。
4. 运行离线合同测试：

   ```bash
   cargo test -p node-service conformance --locked
   cargo test --workspace --locked
   ```

   conformance 模块覆盖三类 driver 的共同 workflow 形状、固定路由、唯一 owner、readiness
   和失败诊断；workspace 中的 journal/engine 测试进一步覆盖 identity 冲突、timeout/unknown
   fence、cancel acknowledgement 与 restart no-replay 语义。driver 的 socket/HTTP 测试仍使用
   deterministic local test server。
5. 只有离线报告通过后，才在目标机器上启动单一节点服务：

   ```bash
   cargo run -p roboguide-node -- \
     scenarios/extension-conformance-v0.1/node.toml
   ```

   将 `server_endpoint` 改为 Controller LAN 地址；Local facade endpoint 保持节点本机
   loopback 或 Unix socket。RoboGuide Server 只发送 canonical intent 和已 Commit 的
   resource IDs，不能下发 Local How。
   State sampling 失败只会让旧 record 过期，不会改变 health/readiness 或触发 recovery；Memory
   provider 声明只建立 discovery contract，exchangeable bytes 仍必须经过 Artifact CAS。
6. 在真实设备上单独执行 health/readiness、execute/status/cancel 和断电/重启演练，保存
   facade 的请求/状态证据，再把配置部署到生产节点。新增设备不需要修改或重新编译
   RoboGuide；只有当本地接口无法由现有 HTTP、dynamic gRPC 或 MCP driver 表达时，才需要
   在 Local EAIOS 侧补一个 facade。

## 不能由离线 conformance 宣称的能力

离线测试不能证明真实 endpoint 的认证、TLS、protobuf descriptor 与服务实现一致性，不能
证明 vendor 状态值、取消语义、超时边界、幂等行为、物理动作已经安全，也不能证明
localization/map frame、硬件急停、碰撞避免、动力学限制或 Local Safety。它同样不能替代
真实网络断开、进程崩溃、facade 重启和“请求可能已经触发物理动作”时的故障注入。真实硬件
验证必须确认：一次 execute 的物理副作用与唯一 execution identity 对齐，status 能够在
重启后无歧义地对账，cancel 只在 Local EAIOS 确认后报告 terminal，未知状态保持
ReconciliationRequired，且任何重试都不会危险自动重放。
