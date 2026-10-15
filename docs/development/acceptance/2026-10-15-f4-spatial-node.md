# F4 Spatial 私有 Local How 与正式 Node/CAS 验收

日期：2026-10-15。状态：本文件切片通过，F4 仍在实施。没有设备、模型调用、模拟器或实验。
本次范围为真实文件 CPU 计算、Node 配置/工作流、Artifact CAS 和独立 verifier；使用显式
trusted-loopback compatibility profile，不声称远程 typed-map provider 权威已经闭环。

## 实现与原始身份

`integrations/spatial-provider/roboguide_spatial_provider/http_service.py` 提供固定私有 Unix HTTP，
0600 socket、独占 writer、实际持久 UUID、原配置/代码身份和不可变 attempt admission。
相同 attempt 重试只查原结果；同 revision 不接受另一个 producing attempt。服务重启查询
原 CLI/worker，不重做计算。Changed code/config 的旧 backend 只允许显式 readonly。

`node_profile.py` 根据部署提供的任意 Node ID、endpoints、semantic profiles 和文件路径输出
正式 Node config v0.8。它不启动进程或创建设备。多个配置 slot 使用同一 canonical build /
publish workflow；Node 的 selected artifact context 必须只包含当前 slot。服务没有 catalog
写权。Node Service 完成 prepared-output 冻结、原字节 CAS 上传和 manifest 发布后，才能报告
publish execution Completed；Mission 仍须等待独立 verifier。

当前 Spatial package source digest 为
`sha256:63d11da65a1f73cd10f4424e1fac622c37e512f742864565e23ba573a2180cc8`，与实际
`computed.json` 中 digest 相同。Controller binary SHA-256 为
`8c029b96b39a51efffc91f28c2f7d1a9a3a6fe8d611b21d8369ffb9e2d37ef46`；Node 为
`351b2c7d8ae55861121f39eb799ccc24e06bb1119789f6ea661861db8595dce0`。本切片不改 Rust。

来源仍为已归档 Open3D DemoICP 原始 PCD 文件，两个文件 SHA 分别为
`e1e100802c29ef454c6b523084668ee0e2f365ec52eaeebe79ae804c20447b15` 和
`a4c3dc0ad7b1279736491b9b2638991d4c808605997be4f9ab174c24a9fa6e52`。独立 Python 3.11 /
Open3D 0.19.0 / NumPy 2.4.6 / SciPy 1.17.1 运行；依赖及核心 binary identity 沿用
[文件验收](2026-10-15-f4-spatial-files.md)与[原始预算验收](2026-10-15-f4-planar-and-clock-budget.md)。

## 实际进程结果

`/tmp/rg-spatial-node-20261015-c/result.json` passed。实际 1 个软件 compute Node、1 个 Native
worker、1 次 Node-owned CAS publication、1 个独立 verifier 进程；设备/模型/模拟器均为 0。
本次 bundle 67354 bytes，SHA-256 为
`e597cd9bed9b2ac60ff5cf6c63a811d0525fa593266742ca15f8f3b2f8baacd7`，含 2129 个观测点、19 个
分块。独立进程只使用标准库，重新读取原始文件、CAS 下载内容、全部 tile hashes、实际 PLY
点数/有限坐标/bounds、frame/unit、刚体变换、固定 anchor 和登记的质量策略；不 import provider。
这证明软件来源/产物完整性，几何精度仍以先前独立坐标验收为限，不是当前物理世界真值。

Mission 在 publish execution Completed 后保持未完成；Controller 与 Node 都实际退出并重启，
原 worker-started 和两份 Local How admission 字节未改变。Catalog 原 source Task/LocalSystem/
Node 不变；verifier evidence 通过公开 API 提交后 Mission 才 Completed，所有 reservation 释放。
重复 evidence 返回原 receipt，没有第二次物理或软件计算。实际篡改一字节 bundle 被独立 verifier
拒绝；把原 exit proof PID 改成另一个 PID 时，Local How 返回 UNKNOWN，恢复原证据后才可读为
COMPLETED；查询没有启动 worker。

`/tmp/rg-spatial-local-20261015-d/result.json` passed。实际 3 个 Native attempts，其中 2 个计算
完成、1 个中断。取消 response 为 RUNNING/accepted，实际原 worker 被回收后才 CANCELLED，
对应 `/proc/PID` 不存在。另一次计算期间 SIGKILL facade，恢复同一真实 backend UUID 后观察原
CLI/worker 完成，原 worker identity 未变。Socket 权限为 0600。没有 Node 或设备参与这份局部检查。

首次 `/tmp/rg-spatial-node-20261015-a/` 因错误 typed-map provider schema 在 Node 启动校验失败；
日志保留，后改为声明实际 native 文件内容 schema。`-b` 跑通早一份源码，`-c` 是当前源码验收。
旧 `/tmp/rg-spatial-local-20261015-a/`、`-b/` 的失败日志保留；没有把历史通过记录重命名为当前结果。

复现命令（输入归档和独立 Native venv 已存在；输出目录必须新建）：

```sh
uv run python tools/quality/check_spatial_node_processes.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --profile /tmp/rg-spatial-local-20261015-c/profile.json \
  --source-root /tmp/roboguide-spatial-provider-inputs-20261015/extract/DemoICPPointClouds \
  --output /tmp/rg-spatial-node-NEW
uv run python tools/quality/check_spatial_local_processes.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --source-manifest /tmp/roboguide-spatial-provider-inputs-20261015/source-manifest.json \
  --policy /tmp/roboguide-f4-spatial-files-20261015-monotonic/pair-request.json \
  --output /tmp/rg-spatial-local-NEW
```

## 验证边界与后续

23 个 Spatial 纯合同测试通过，包括 schema/path/selector 注入、第二 writer、changed config
只读、永久失败的原 attempt 重试和多 slot 配置。Ruff、格式、函数文档检查通过；按维护模块
显式路径运行 strict mypy，216 sources 通过。未将 Native 依赖装入根 MI 环境。

额外全根 mypy 发现旧的 evaluation/Memory `conftest` 重名；排除重名继续时，有 evaluation 对
MI 新 transport/JSON 类型的 21 处不一致。这不属于本切片通过范围，F10 总验收必须修复；
没有把它写成全根 typing 通过。完整 Python 回归 2486 项通过（collection 与完整运行均已核对），
全维护路径 Ruff/format 349 files 通过，函数文档和 `git diff --check` 通过。

取消证明仅为软件 child 的实际退出，缺 Controller exact-command/generation safe-state 接线
时，不能作为设备停止或 ownership 释放依据。真实 stop、设备身份/几何与物理连续性保持
HardwareEvidencePending。远程 typed-map provider/session/当前 publishing attempt、Group grant/
撤销 ACK、pre-Execute preparation、handoff、active-map 和 GC 继续实施。此记录不关闭 F4 或后续阶段。
