# F4 平面占用文件与原始计算预算

日期：2026-10-15。状态：当前计算切片通过；完整分布式 Memory/Spatial 仍在实施。

## 实现边界

独立 Spatial provider 接受明确的 `spatial-occupancy-job/v0.1`，使用有界、策略配置的 SE(2)
correlative search、rigid ICP、固定 anchor pose graph 和不可变稀疏 tiles。三维 PLY/PCD
保留原 FPFH/RANSAC/ICP 路径，两个 profile 共用 durable once-only job/worker。
没有设备名称、Node 数量或任务图分支。

occupancy 的 -1/intermediate 值和缺失 cell 保持 unknown。free 只有在显式输入 free square
完整覆盖输出 cell 时成立；occupied footprint overlap 与 free 矛盾保留为 conflict。
parent 的 conflict 不作为无歧义配准点。SE(2) 不产生高度、完整 SE(3)、当前定位、导航安全
保证或 covariance。闭合 schema 见 [file-provider 合同](../../../contracts/spatial/file-provider-v0.1/README.md)。

原 job 在 source snapshot 前冻结实际 Linux boot/CLOCK_MONOTONIC domain 和 deadline；
父子进程共用同一原始预算。audit wall rollback 不能延长 work，换 host/reboot 不能重启
旧 job。完成的原时间证明在重启后可只读核对，历史 wall-only 证据不补成新证明。独立
worker 的原始 alarm/CPU/VM/file-size 限制保持；重试不重启 native worker。

## 实际文件与结果

CPU runtime：Python 3.11.16、Open3D 0.19.0、NumPy 2.4.6、SciPy 1.17.1；结构验证器
jsonschema 4.26.0。root Mission 环境不安装 native geometry。零设备、零模拟器、零模型调用。

输入来自先前已归档的真实 Open3D `cloud_bin_0.pcd`，198835 个点，SHA-256
`e1e100802c29ef454c6b523084668ee0e2f365ec52eaeebe79ae804c20447b15`。
计算投影为 105 个 occupied cells，没有从点云推断任何 free cell。三份正向输入是这个原始
文件的三种坐标/单位编码，用于独立变换检查，**不是三个独立传感器或当前设备观察**。
原始三份不同点云文件的增量检查另见前一记录和本次三维回归目录。

当前源码 digest：`sha256:561e3922ca6bed26a590f8d78acd8b0e06c4a0df91b69cb54ade54250ec35d5d`。

| 检查 | 实际证据 |
| --- | --- |
| 平面实际运行 | `/tmp/roboguide-f4-occupancy-files-20261015-monotonic/result.json` passed=true |
| 独立坐标检查 | 两组逆变换误差分别为 `4.47545209131181e-16m`、`3.8659043403122315e-16m`，角度均约 `5.55e-17rad`；只是同源坐标校验 |
| 平面回环 | 3 条实际配准边；SE(2) 和厘米转换保留 |
| 平面 bundle | 5280 bytes，`sha256:1a12f8091a4820cd59bbada67649f8598471408883537fc901ef76240e7e1353` |
| 平面增量 | 父 bytes 不变；4573 bytes，`sha256:ef6d92681a7308ee19b382c58f8db1dc19d54c116255c4df4a2f3f735ac6aeb3` |
| 反例 | 损坏 parent 和非刚性低重叠 unavailable；明确人为注入的 free 矛盾保留 conflict，不是设备证据 |
| 原始超时 | 1 秒计算为 Unknown；同 job retry 的 intent/worker bytes 不变 |
| 三维回归 | `/tmp/roboguide-f4-spatial-files-20261015-monotonic/result.json` passed=true；独立误差约 `0.0015062m / 0.0009323rad`，退化/低重叠/损坏/超时仍拒绝 |
| 原始时间 | 两套 native 检查均验证 `original-host-monotonic-budget`；unit checks 验证 wall rollback/reboot/历史证明不能续期 |

12 个 Spatial 纯合同测试及当前完整 Python 2475 项通过。strict mypy 212 sources、
Ruff/format 222 files、函数文档检查通过。5 份 schema 和 32 份实际请求/map/grid 经独立
jsonschema 验证；5 类恶意字段/类型/维度/covariance 被拒绝。记录位于平面目录的
`schema-validation.json`，包含 schema hashes 和实际 document 列表。
早期 planar/code/clock 版本的目录保留；当前验收只使用上述明确 monotonic identity。

```sh
uv run python tools/quality/check_occupancy_provider_files.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --source /tmp/roboguide-spatial-provider-inputs-20261015/extract/DemoICPPointClouds/cloud_bin_0.pcd \
  --source-sha256 e1e100802c29ef454c6b523084668ee0e2f365ec52eaeebe79ae804c20447b15 \
  --output /tmp/roboguide-planar-file-check-new
uv run pytest -q integrations/spatial-provider/tests
uv run python tools/quality/check_spatial_contracts.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --point-root /tmp/roboguide-f4-spatial-files-20261015-monotonic \
  --planar-root /tmp/roboguide-f4-occupancy-files-20261015-monotonic \
  --output /tmp/roboguide-spatial-schema-receipt-new.json
```

输出目录必须新建。正式 Node workflow/CAS publication、当前 Group grant/撤销/pre-Execute、
handoff、active-map/GC 和 F5–F10 继续实施；本记录不声明整体 SoftwareComplete。
