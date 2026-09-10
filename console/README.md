# RoboGuide Mission Console

> **状态：开发中（experimental）。** 布局、功能与事件映射仍在快速演进，
> 未冻结；演示剧本是手工构造的叙事，不是回归基线。

只读的任务旅程可视化控制台：展示 RoboGuide 接受任务后，事件证据流经
Mission Intelligence → Control Plane → Execution Group → Runtime → Nodes →
Physical World 各层的真实效果。支持**多 Mission 并发**与**整机集群视图**。
它不是新的 Control / Runtime / State 权威，只消费既有 HTTP API
（`/v1/events`、`/v1/inventory`、`/v1/missions` 等）。

## 运行

```bash
# 静态控制台 + 同源代理（Controller 默认 http://127.0.0.1:8080，Mission Service 默认 8070）
python3 console/serve.py

# 自定义上游 / 监听地址
python3 console/serve.py --bind 0.0.0.0 --port 8095 \
  --controller http://127.0.0.1:8080 --mission http://127.0.0.1:8070
# 或环境变量 ROBOGUIDE_CONSOLE_CONTROLLER / ROBOGUIDE_CONSOLE_MISSION
```

打开 `http://127.0.0.1:8095`。代理仅转发浏览器已发起的请求并原样返回 JSON
（含 query string），不添加任何 RoboGuide 语义。

## 两种模式

- **演示回放**：内置三条与真实事件字段完全一致的剧本（`js/replay.js`）——
  「集群双狗地图共享」（6 设备、3 Mission 并发，含
  建图→发布 CAS→导入→强定位验证完整 Spatial Memory 链路）、phase1
  跨节点交付完整闭环、节点租约过期触发
  `Reconciliation → RecoveryCandidates → RecoveryScheduling → Proposal →
  Commit → Rebind` 的恢复链路。无需运行任何系统即可演示。
- **实时系统**：连接真实 Integration Server/Controller。先启动：

```bash
cargo run -p integration-server -- 0.0.0.0:50051 ./var/controller-events.sqlite3 127.0.0.1:8080
```

  控制台按 `after` 游标增量轮询 `/v1/events`，每 6 秒刷新 `/v1/inventory`
  集群网格，并对**每个**未终态 Mission 每 2.5 秒轮询权威 lifecycle/任务/关系。
  事件流中出现的陌生 Mission 会被自动发现并跟踪（objective/DAG 骨架来自
  `/v1/state/records`）。可选择 `scenarios/` 内的版本化 MissionPlan 通过既有
  `POST /v1/missions` 提交真实任务，或 `POST /v1/missions/<id>/cancel` 取消。

## 多 Mission 与集群视图

- 舞台最多并排 3 个 Execution Group 框（各 Mission 一个，按创建顺序着色：
  紫/青/橙），每个框独立显示 lifecycle（ACTIVE/BLOCKED/RELEASED）
- Runtime 槽位按 `mission/task` 复合键区分，左侧色条标识所属 Mission
- 左栏 Mission 列表可点击聚焦：聚焦 Mission 的 DAG 显示在 Task DAG 面板、
  任务芯片显示在 Mission Intelligence 层、Group 框加亮
- 节点层渲染全部已接入设备（最多 8 张卡），右上角彩点徽章 = 该设备正在
  服务哪些 Mission（来自调度选择/绑定/重绑定事件）
- 调度日历按 `mission/task` 复合键展示，任务名冲突时自动加 Mission 前缀

## 数据源（全部来自代码实现）

| 控制台数据 | 真实来源 |
| --- | --- |
| 事件证据流 | `GET /v1/events?after=<seq>`（`core/state` SQLite event log） |
| 集群节点卡 | `GET /v1/inventory`（Shared Node State 投影） |
| Mission/Task/Relation 状态（逐 Mission） | `GET /v1/missions/<id>`（orchestration 投影） |
| 调度日历 | `GET /v1/scheduling-reservations`（Control calendar） |
| Mission objective/DAG 骨架（自动发现） | `GET /v1/state/records`（只读 federation） |
| 事件语义映射 | `core/domain::EventPayload` 逐变体映射（`js/model.js`） |
| 演示剧本字段 | 同 `EventPayload` serde 形状（`js/replay.js`） |

事件载荷是外部标签枚举（`{"CandidatesMatched": {...}}`），未知变体会以
"系统" 类别降级展示，不会被丢弃。点击事件行可查看完整 payload JSON。

## 目录

```text
console/
├── serve.py          # stdlib 静态服务 + /proxy/controller|mission 反向代理
├── index.html
├── css/console.css
├── js/
│   ├── model.js      # 55 种 EventPayload 变体 → 层级/语义链/面板动作
│   ├── stage.js      # SVG 分层舞台 + 光包动画引擎
│   ├── panels.js     # Mission/DAG/调度日历/恢复阶梯/State&Memory/事件流
│   ├── replay.js     # 演示剧本（真实事件字段）
│   ├── api.js        # 只读 HTTP 客户端
│   └── main.js       # 组合根：模式切换、状态机、Live 轮询
└── scenarios/        # 仓库 scenario MissionPlan 副本，供实时模式提交
```

零第三方依赖、零构建步骤；不引入 Node 工具链。
