# 通用 Scene / physical World 选择性整合 · 2026-11-12

本批以 `516dfdf438b5b74526d043f294e64bb61c6a311e` 为基础，选择性整合
`a9f1268bc3052c4183f2f41b0035c8627b1951f6` 和
`f686ce22ed1cc018a923fd4b75f330b4f605d40a` 的通用观察增量。
不生成固定实体分工、巡逻计划或人工到达发布器；原始场景与现场历史留在原分支。

## 纳入的完整链路

- 现有 Connector 显式 `--physical-entity ENTITY_ID`，配合 `--node-id NODE_ID` 启用
  原 sensor worker 内的 Scene 采样。原 chassis worker 独立，新增 GET 只读取缓存。
- `physical_observation` 只投影来源报告的姿态、电量及 Mapping 接收信息。
  Node 健康、资源和执行内容保持在原通道，不改名为物理 World。
- `scene_targets` 的固定 state → regions → state 读取共用原五秒预算；原地图绑定
  前后相同才可发布。原 canonical `definition_json` UTF-8 字节及摘要穿过外层 JSON，
  未验证的定位、共享 frame、到达和设备停止保持 false/unknown。
- Mission 显式 schema/profile 校验实体、来源、channel、session/sequence、摘要与几何。
  原采样预算继续消耗 Controller 保留时间和本机采集/校验耗时；Memory 等后续读取
  完成后，在冻结 snapshot 前再次检查。没有跨主机时钟相减，也不因 GET 或外层接收续鲜。

部署可参考 `physical-world-node.fragment.toml` 和 `scene-targets-node.fragment.toml`。
这两份显式片段使用部署的 ENTITY_ID，并须与已接纳的原实体/Node 路由一致；
不能直接把占位符当作已登记设备。MI 使用其现有 `grounding_world_payload_schemas`
显式接纳所需 schema；源码整合不启用现场配置。

合入的 Connector 以较早版本为基础只增加两个导入、三条 World GET、实体参数和
采样接线，没有引入照片任务类、上传路由、队列或其配置。旧 camera、origin、Pilot
接线保留。未知 World 路由和选择其他实体的 query 返回 404，不触发源采样。

## 当前候选的验证

- Mission `test_grounding_context.py` 与 `test_scene_grounding.py`：109 passed。
- 独立 `robonix-task` unittest：91 passed，18.547 秒；包含原任务/取消/观察器回归。
- 四个新增 World route 测试通过实际 loopback HTTP 验证禁用、缓存和来源隔离。
  全链用例使用真实 Scene producer、Connector 与 MI HTTP reader，只有 Controller
  State envelope/storage 是显式 fixture；原 definition 字节/摘要保留，篡改被拒。
  这不是实际 Core State 接纳/持久化或硬件定位验收，原生端到端门禁需独立完成。
- Ruff、39 文件格式、Mission 四文件与 adapter 十五文件 strict mypy、函数文档通过。

日志保留在 `/tmp/rg-integration-plan-20261112-dhbioz6i/implementation/scene-*.log`。
没有模型调用、设备动作或现场部署；本批结果不替代合并后统一源码的检查。

独立 adapter 保持部署边界，不进入 chat 默认 wheel 或依赖。对应检查命令仍采用
`integrations/robonix-task/README.md` 的独立 PYTHONPATH、grpc/protobuf 和 unittest。
