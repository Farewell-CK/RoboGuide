# NAVI 视觉服务源码

这些文件来自当前实测的 DINOv2 服务，与 Android APK 分开运行。整合 App 继续使用相同 HTTP 接口和数据库，不需要另起一套线上服务。

| 文件 | 用途 |
|---|---|
| `cpu_gateway_snapshot.py` | 部署时固定的 CPU 网关源码；SHA-256 由 manifest 校验 |
| `navi_gpu_gateway.py` | 在独立 CUDA 环境中复用上述 API、预处理和 768 维归一化 CLS 特征；FP32，不静默降级 |
| `source-manifest.json` | 网关源码及 DINOv2 权重/配置哈希、模型 revision |
| `gpu-freeze.txt` / `cpu-freeze.txt` | 实际 Linux Python 3.10 环境依赖快照 |
| `setup_gpu.sh` / `manage_gpu.sh` | 原测试服务器运维脚本，保留作复现记录；不是通用一键安装程序 |
| `benchmark_gpu.py` | CPU/GPU 相同图片的余弦相似度、误差和 HTTP 耗时对照 |

## 当前部署约定

原运维脚本默认使用 `/home/seaway/sdb/niulinqiang/roboguide_demo`，服务目录 `gpu-compare` 与独立的 `navi-cpu-env`、`navi-gpu-env` 同级。脚本依赖已有 CPU 环境、模型缓存、原始网关源码以及 `manage_gpu.sh inspect` 生成的环境记录。迁移到其他机器时需按本地目录调整，不能直接运行并假定已完成部署。不要将服务器私钥、密钥、图片样本或虚拟环境提交到仓库。

GPU 启动器检查自身所在目录的上一层有 `navi-gpu-env`，并要求 `CUDA_VISIBLE_DEVICES` 只暴露一块已获分配的 GPU。模型为 `facebook/dinov2-base`，固定 revision 见 manifest；启动前须下载对应模型到 `HF_HOME` 缓存。当前推理服务仅监听服务器回环地址 `127.0.0.1:8002`，CPU 对照为 `127.0.0.1:8000`。跨设备访问沿用已有转发/Tailscale，不建议直接暴露无鉴权网关。

## App 对接

- 健康检查：`GET /health`，包含 `status`、`device`、`dimensions` 等实际能力。
- 普通特征：`POST /encode/preprocessed`。
- 导航特征：`POST /encode/navigation`。
- 图片采用 multipart 字段 `image`；返回 `embedding` 数组与 `dimensions=768`。完整请求参数见网关源码及 `navi_indoor/lib/services/dinov2_service.dart`。
- 本次仅开启 DINOv2；YOLO、行人消除和 GPT 验证不是默认部署能力。

## 对照测试

将至少 3 张经授权的 PNG 测试图放入本目录的 `samples/`，在同时可访问 CPU/GPU 回环服务的 Python 环境执行 `python benchmark_gpu.py`。脚本先预热，交替请求，再记录中位数/P95 与向量误差。没有运行对照测试时，不将历史测试结果作为本次整合性能证明。
