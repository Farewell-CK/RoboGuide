# F4 typed-map 字节副本与实际 prepared consumer

本切片修复真实文件链路暴露的 409：typed map 只有 Spatial manifest，原通用 replica 写入
却要求再有一份 generic manifest。现在原 Spatial publication 保持唯一 manifest 权威；
事务写锁内将副本请求绑定为 `MapMemoryReplicaRecorded`，保留完整原 map 和消费端 provider。
原 source、frame 或 provider 身份改变均拒绝。回放重新核对 published source；Imported
保持单调。HTTP `replicas` 是字节导入，`map_replicas` 是原地图工作流证据，二者分开返回。

Controller wrapper v36 / event codec v25 保护该演进；原 Node Contract v0.9、config v0.10、
journal v9 和 prepared raw-map input 沿用。补齐 catalog v0.11 的 config v0.9 JSON Schema，
拒绝在旧配置中使用新 availability。实际 MissionPlan 明确保留 `verifier: null`。

## 实际进程证据

当前 `/tmp/rg-spatial-consume-20261015-c/result.json` 为 `passed=true`。0 设备、0 模型、
0 仿真器；同主机 2 个真实软件 Node、不同证书、公开 enrollment、实际三入口 mTLS。
一个真实 native CPU worker 从两份既有 PCD 生成 2129 个观测点、19 个 tile；Node 经 CAS
发布，再由另一 Node 完成 Prepare→原 import→当前内容 Probe→Execute。第二次实际 CPU
计算使用导入的地图包；两个独立进程分别重读原 source/tile 和消费端内容、计算结果。
Task 满足来自独立 verifier；本地 Completed 不直接满足 Mission。

| 检查 | 实际结果 |
| --- | --- |
| bundle | 67348 bytes，SHA-256 `4ffea633707e9d981bc82d7339f2d72c2c21ec3201f19fbecfe08ec4cfde7dda` |
| 来源 | 原 build Task/attempt、LocalSystem/provider、完整 raw map 在 preparation/import 中保留 |
| 重启 | 生成后和消费后分别重启 Controller；重启源 Node、接收 Node 与接收 backend |
| 重启后 | 两 Node 的新 registration receive time 且 Reachable；完整字节副本 API 与原值相同 |
| 不重做 | 原 backend identity/import、完成的 file job、Node preparation/budget/import SQL 行 digest 不变；原 native worker 仍只有一次 |
| 负向 | 错 Node/session/provider/schema、旧 map 写、viewer 写被实际 403 拒绝；完整内容策略删除/篡改/降级被拒 |
| 清理 | 10 个本次自有进程全部 reap；未操作其它服务 |

Controller binary SHA-256 `de2939459627f5ab7ee894c554c7adbcf8e610a4cb820104f9ce6fd8bc7fdfe7`，
Node binary `b63e8aad44e68c2ebfc42efa629eedffa1a3b09a1f549fcb2039f1ee08bdff76`。
结果保留 checker/module/config identity，依赖环境沿用独立 Python 3.11/Open3D 环境。
原失败 `/tmp/rg-spatial-consume-20261015-a` 保留；`b` 是首轮真实字节链路通过记录，
其计划缺少严格 schema 要求的显式 null，当前 `c` 已修正并重新验收，未改写原产物。

复现当前正向链路：

```bash
/tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  tools/quality/check_spatial_node_processes.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --profile /tmp/rg-spatial-mtls-20261015-f/profile.json \
  --source-root /tmp/roboguide-spatial-provider-inputs-20261015/extract/DemoICPPointClouds \
  --output /tmp/rg-spatial-consume-<new-id> --mtls --memory-consumer
```

`schema-validation.json` 使用本地离线 jsonschema 4.26：实际 MissionPlan、两份不同版本的
Node TOML、完整 typed map、preparation/control/receipt/availability 通过；15 项非法变体拒绝。
元数据单元测试另外覆盖同 Node 的多个消费 provider、篡改 frame、禁止 generic alias、
损坏事件回放拒绝和 archive 保留，不计为设备证据。

`upgrade-gates.json` 的 4 项实际拒绝通过：旧 Node journal v8 binary 拒绝 v9，手工回退
SQLite header 后仍被 durable marker 拒绝；旧 Controller v35 binary 拒绝 codec v25，
另在 0 Node / 0 event 的实际 v36 checkpoint 上拒绝 wrapper。原 authority 行 digest 均未变。

Rust workspace 813 passed / 2 ignored；workspace Clippy、private Rustdoc、fmt 通过。
四个变更 Python checker 的 Ruff、strict mypy 和函数文档检查通过。日志位于
`/tmp/roboguide-f4-typed-consumer-{workspace,clippy,rustdoc,build}-20261015.log`。

本切片只证明地图 bytes 被真实跨节点导入和计算。active-map 选择、语义空间消费、当前设备
定位、完整 Group lifetime/handoff、GC 和 F5–F10 继续实施；不得据此报告 F4 或整体完成。
