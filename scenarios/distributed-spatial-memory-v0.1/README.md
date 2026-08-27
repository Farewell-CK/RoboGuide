# Distributed Spatial Memory v0.1

该场景验证两个节点在不同 Mission 中双向交换不可变地图 revision：

- A→B：`mission-a-build-publish.json` 后执行 `mission-b-import-verify.json`；
- B→A：`mission-b-build-publish.json` 后执行 `mission-a-import-verify.json`。

`artifact_operation` 明确区分 `prepare-output`、`publish`、`import` 和 `verify`，静态
`artifact_slot` 只选择部署配置中的 binding。成功链应在 State Catalog 中留下
`Published`，以及 consumer Node 的 `Staged → Imported → Verified` evidence。

[`dog-a-node-v0.1.toml`](dog-a-node-v0.1.toml) 和
[`dog-b-node-v0.1.toml`](dog-b-node-v0.1.toml) 是该场景的 Node Config v0.3 部署输入。
两者都显式注册 `spatial.map.build@v0`、`spatial.map.publish@v0`、
`spatial.map.import@v0` 和 `spatial.localization.verify@v0`，但 artifact binding 保持非对称：
dog-a 只构建 `map-a/r1`、消费 `map-b/r1`，dog-b 则相反。不要用通用
`config/node.toml` 启动本实验。两节点的 Control-visible resource identity 也分别使用
`dog-a-spatial-compute` / `dog-b-spatial-compute`，避免把不同机器上的本地算力误报成同一
全局资源。

```bash
cargo run -p roboguide-node -- \
  scenarios/distributed-spatial-memory-v0.1/dog-a-node-v0.1.toml
cargo run -p roboguide-node -- \
  scenarios/distributed-spatial-memory-v0.1/dog-b-node-v0.1.toml
```

两份配置假定 dog-a/dog-b 的 Local EAIOS HTTP endpoint 分别监听 `127.0.0.1:18101` 和
`127.0.0.1:18102`，实现 `/v1/health`、`/v1/executions`、
`/v1/executions/status` 与 `/v1/executions/cancel`。RoboGuide Node Service 会把受控的
`artifact_path` 和 canonical `invocation` 映射进 execute request；实际建图、导入和定位验证
仍由 Local EAIOS 执行。Node 状态与 artifact cache 写入仓库已忽略的 `artifacts/` 目录。
健康检查使用独立的 2 秒 HTTP connection，保证单次探测明显短于 Server 的 15 秒 lease；
执行 workflow 保留 30 秒 timeout。Artifact 数据面另有 3 秒连接和 30 秒 read-idle timeout，
避免断网或对端停止前进时让 Task 无限停留在 Active。

配置中的 RoboGuide gRPC 与 Artifact endpoint 默认也使用 `127.0.0.1`，仅用于单机双 Node
smoke。部署到两台机器时，必须在各自的部署配置中把 `server_endpoint` 和
`[artifacts].endpoint` 改为同一个 Controller LAN 地址，例如
`http://<controller-lan-ip>:50051` 与 `http://<controller-lan-ip>:8090`；Local EAIOS 的
`127.0.0.1:18101/18102` 保持不变。Controller 的 gRPC/Artifact listener 相应绑定所有实验
网卡，Control HTTP 可以只绑定 Controller 本机：

```bash
cargo run -p integration-server -- \
  0.0.0.0:50051 ./artifacts/distributed-spatial-memory-v0.1/controller.sqlite3 \
  127.0.0.1:8080 0.0.0.0:8090 \
  ./artifacts/distributed-spatial-memory-v0.1/controller-cas \
  scenarios/distributed-spatial-memory-v0.1/actor-placement.json
```

启动 Integration Server 时可额外传入 [`actor-placement.json`](actor-placement.json)。这是
Control 的部署策略输入，把四个 Mission 中的逻辑 `robot-dog-a/b` 分别约束到物理
`dog-a/b`，从而让双向实验真正验证跨节点共享，而不是依赖 NodeId 排序碰巧选中某一只狗。
该约束只收窄首次 Matching 的 Candidate Set；Proposal、Commit、Group Bind 和后续 actor
continuity binding 仍走正常 Control 流程。配置启用后采用 strict coverage，提交的每份
MissionPlan 必须恰好覆盖其中全部 Actor；任何 MissionId/ActorId 拼写错误都会拒绝提交，
不会退回普通确定性 Matching。

两只 Node 注册并保持 Online 后，必须按 publish 完成后再 consume 的顺序提交，不能把四份
Mission 并发提交后期待 Runtime 猜测 producer output。每次提交后通过 Mission API 等待
`Completed`，并检查 Catalog 中对应 revision 与 consumer replica evidence：

```bash
curl --fail-with-body -X POST http://127.0.0.1:8080/v1/missions \
  -H 'Content-Type: application/json' \
  --data-binary @scenarios/distributed-spatial-memory-v0.1/mission-a-build-publish.json
curl http://127.0.0.1:8080/v1/missions/mission-map-a-build
curl --fail-with-body -X POST http://127.0.0.1:8080/v1/missions \
  -H 'Content-Type: application/json' \
  --data-binary @scenarios/distributed-spatial-memory-v0.1/mission-b-import-verify.json
curl http://127.0.0.1:8080/v1/missions/mission-map-b-consume

curl --fail-with-body -X POST http://127.0.0.1:8080/v1/missions \
  -H 'Content-Type: application/json' \
  --data-binary @scenarios/distributed-spatial-memory-v0.1/mission-b-build-publish.json
curl http://127.0.0.1:8080/v1/missions/mission-map-b-build
curl --fail-with-body -X POST http://127.0.0.1:8080/v1/missions \
  -H 'Content-Type: application/json' \
  --data-binary @scenarios/distributed-spatial-memory-v0.1/mission-a-import-verify.json
curl http://127.0.0.1:8080/v1/missions/mission-map-a-consume

curl http://127.0.0.1:8090/v1/maps
```

`map-a/r1` 与 `map-b/r1` 是不可变身份。重复整场实验时应使用新的 revision，或在明确不需
保留证据时从全新的 Controller database、CAS 与两只 Node state/cache 开始；不得覆盖已有
Published revision。

场景是软件/Fake Node 验收 fixture，不代表真实机器狗已经完成坐标标定。地图 bytes 由
Artifact data plane 传输，MissionPlan 只携带预分配的 map/revision scalar reference。
