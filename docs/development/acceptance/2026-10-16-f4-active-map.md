# F4 Active Map、交接与保留门禁

状态：软件切片通过；物理定位、硬件连续性和硬实时停止保持 `HardwareEvidencePending`。

本切片把 Spatial Memory 的 immutable map revision 接入配置的 Local Memory Provider。Provider
只读取实际 CAS ZIP bytes，流式核对外层 SHA-256、manifest、来源 lineage、单位、root frame、
tile digest/bounds 和 `unobserved_space=unknown`。它只返回 tile 语义命中；`localization` 永远
为 `null`，不能把文件地图当作当前设备位姿。

选择上下文固定包含 map/revision/content digest/size/frame/anchor、Task attempt、execution、
current-use lease generation、Node 和 Group。选择及查询都重新核对当前 lease；generation CAS
拒绝旧选择。重启会把 active head 变成 `stale_after_restart` 并撤销 active-map 引用，不会续期
原预算。替换、释放和 handoff 保留代数历史；handoff 先持久化完整软件 stop barrier，再原子
写入 target head。这个 stop barrier 只说明软件进程和命令状态，不能证明设备停止。

GC 需要执行、import、use lease、active map、lineage、replica、snapshot、archive 八类引用均
有显式正向证明，且当前 durable active-map ref 已失效；否则只返回 `blocked`。删除前重新从
CAS path 读取实际文件属性和字节，任何 symlink、压缩 ZIP、重复 entry、digest/size/manifest
不一致都会拒绝。

## 实际进程证据

命令：

```bash
uv run python tools/quality/check_spatial_node_processes.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --profile /tmp/rg-spatial-use-20261015-b/profile.json \
  --source-root "$(cat /tmp/rg-spatial-use-20261015-b/source-root.txt)" \
  --output /tmp/rg-spatial-active-map-20261016-c --mtls --memory-consumer
```

结果文件：`/tmp/rg-spatial-active-map-20261016-c/result.json`。

该次链路实际通过：`software_nodes=2`、`actual_native_workers=1`、`independent_verifier_processes=2`、
`devices=0`、`models=0`、`simulators=0`；Active Map selection/query、CAS tile digest、receiver
backend restart、旧 active head fence、Controller/Node restart 无第二 worker、坏 bundle 拒绝和
远程伪造写入拒绝均通过。独立结果中的 `physical_localization` 保持 `HardwareEvidencePending`。

单元测试 `integrations/memory-provider/tests/test_active_map.py` 覆盖实际 ZIP/CAS 校验、压缩
bundle 拒绝、查询幂等、释放、完整 GC 引用门禁以及软件 stop barrier 后的代数 handoff。

## 未关闭边界

本切片没有建立真实设备定位、safe-state/stop certificate、跨 Group 物理 handoff、map fusion、
active-map 多副本共识或实时增量同步。完整 Group 生命周期、跨站点数据面恢复、动态 HA、配额、
运行中抢占、联邦和运维总验收仍按总整改计划继续。
