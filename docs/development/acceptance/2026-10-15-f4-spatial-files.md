# F4 实际空间文件计算切片验收

日期：2026-10-15。状态：当前文件计算切片通过；完整 F4 仍在实施。
零设备、零模拟器、零模型调用。没有生成设备能力或物理定位观察。

## 当前实现与来源

`integrations/spatial-provider/` 使用独立 Python 3.11.16 / Open3D 0.19.0 / NumPy 2.4.6 /
SciPy 1.17.1。正式 root Python 不安装这些 native 库。实际计算执行 FPFH/RANSAC、point-to-plane
ICP、固定 anchor pose-graph 和三维观测点分块融合；父 revision 不变。输入/输出 digest、
单位、frame、原始预算、代码 digest、依赖版本及三个核心 native binary digest 均保留。
指标不会生成 covariance，未观测空间为 unknown，current_node_localization 为 null。

实际输入来自 Open3D `DemoICPPointClouds` 的
[原始发布包](https://github.com/isl-org/open3d_downloads/releases/download/20220301-data/DemoICPPointClouds.zip)。

| 原始文件 | bytes | SHA-256 |
| --- | ---: | --- |
| cloud_bin_0.pcd | 6362965 | e1e100802c29ef454c6b523084668ee0e2f365ec52eaeebe79ae804c20447b15 |
| cloud_bin_1.pcd | 4410901 | a4c3dc0ad7b1279736491b9b2638991d4c808605997be4f9ab174c24a9fa6e52 |
| cloud_bin_2.pcd | 6124949 | 1e68e194ebc1941f0f29764e4daf89340e69b224d2b80db5efbc1373a17f8b4a |

## 实际结果

原始证据目录：`/tmp/roboguide-f4-spatial-files-20261015-reviewed`。`result.json` passed=true；
source manifest SHA-256 为 `fcd0c3c8a1f3d0e965935fed82631bf3666970670ad6b1a17a320ead09660c94`；
算法源码 identity 为 `sha256:ba108095155faaee950fbaccb72e7af1ccbe26b074429777725dc18ce88f5634`。

- 两文件配准产物：67389 bytes，`sha256:0c12615bbbfef072fad9032848ae29770d31f40e4a9dec1f788ffef3e32376f4`。
- 第三文件增量产物：70626 bytes，`sha256:aae960d63b69485bcba9d413ee212fe123fa8f646cb86044129f327955a3becb`；
  原父 bundle 哈希不变。
- 从真实第一文件另生成坐标变换输入，算法没有收到真值矩阵。独立 stdlib 矩阵计算得到估计
  逆变换误差 `0.0015062175909158952m` / `0.0009322553902497844rad`。
- 相同变换文件改用厘米编码并声明单位，误差为 `0.0015062175909166132m` / 同一角度误差。
- 三源完整图保留 3 条实际配准边，回环检查通过。
- 退化平面、非刚性低重叠、父 tile 损坏分别返回 unavailable；没有 computed.json。
- 原 1 秒计算超时为 Unknown。再次提交只观察原结果；intent 和 worker-started bytes 不变，
  没有第二个 worker 或预算续期。正常完成后的同 ID retry 也只观察原产物。
- 7 个纯合同测试覆盖重复 bytes/伪 frame、错误字段类型、解码分配头、symlink/路径逃逸、
  中断原 identity、digest 失败及禁止重算；format/lint/strict typing/函数文档检查通过。

先前 `/tmp/roboguide-spatial-reference-pair-20261015-a-result.json` 保留了首次不受 SDK 支持的
写文件参数导致的 unavailable。修正接口后，当前独立整套检查通过；失败记录没有覆盖或改写。

## 复现

```sh
uv run python tools/quality/check_spatial_provider_files.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --source-manifest /tmp/roboguide-spatial-provider-inputs-20261015/source-manifest.json \
  --policy /tmp/roboguide-spatial-reference-pair-20261015-b.json \
  --output /tmp/roboguide-f4-spatial-files-20261015-reviewed-new
uv run pytest -q integrations/spatial-provider/tests
```

输出目录必须是新的私有目录。公开数据只是软件几何输入，误差不是设备定位精度。
本记录之后的 [Occupancy 与原始 monotonic 预算](2026-10-15-f4-planar-and-clock-budget.md)
已有独立验收，保留本轮 source/code/results identity。正式 Node/CAS workflow、Group grant/撤销、pre-Execute、active-map/handoff/GC
和后续 F5–F10 仍为必需工作；本记录不声称整体 SoftwareComplete。
