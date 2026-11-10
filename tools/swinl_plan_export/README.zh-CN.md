# Swin-L NPU 导出与构建

本目录保存 2026-11-04 手机上验证过的转换代码。原版应用包名保持为
`com.seaway.guideassistant`；所有 Swin-L APK 使用独立包名
`com.seaway.guideassistant.swinl`。

验证后的快速计划输入为 RGB FP16 NCHW `1×3×288×384`，输出为
INT32 mask 和 FP16 confidence，均为 `288×384`。模型仍为原 Swin-L
检查点和 65 类，保留全部编码器和解码器层。

## 关键修复

旧 `qc,qhw->chw` 转换把 `[100,65]` 直接 reshape 为 `[1,65,100]`，
遗漏了分类轴与 query 轴的转置。旧 verified170 的哈希只能证明该错误路径
可重复，不能证明其与原模型相同。`fix_original_projection.py` 为原
640×480 ONNX 单独生成修正版，旧文件和历史 APK 保留。

384 导出通过逻辑等价的按行清零表达式替换 decoder 的索引赋值，避免
ScatterND；掩码仍由每次输入图像计算。只在 FP32 阶段使用 ORT BASIC
折叠常量，然后转 FP16。不要用单张图的计算值替换动态掩码或整数结果。

固定线性权重转为 Gemm；MDLA 不接受的 BMM batch 广播显式展开。
仍使用 dense GridSample、共享权重、16 个成本选取的编译模板、磁盘缓存、
异步预取和原生前后处理。最终应用关闭 Burst、高优先级和逐子图硬件计时。

## 本机重现

导出环境是 WSL Ubuntu-24.04 的隔离 venv
`/tmp/swinl-fast-export-20261104`，主要依赖 torch 2.8 CPU、transformers
4.46.3、onnx 1.17、onnxruntime 1.20.1 和 onnxconverter-common 1.16。
检查点位于 `D:/_work/models/m2f_swinl_mapillary`。脚本内固定的本机输入路径
是历史测试素材，不是相机或导航输入。

在此目录设置 `PYTHONPATH` 为当前目录。导出脚本在 WSL 执行，后两步在
Windows Python 执行。例如输出目录仍使用 `D:/npudaomang/swin/fast384/export4`：

```powershell
wsl -d Ubuntu-24.04 -- /tmp/swinl-fast-export-20261104/bin/python -u fast384/export_candidate.py --width 384 --height 288 --output /mnt/d/npudaomang/swin/fast384/export4
wsl -d Ubuntu-24.04 -- /tmp/swinl-fast-export-20261104/bin/python -u fast384/prepare_candidate.py /mnt/d/npudaomang/swin/fast384/export4
python fast384/rewrite_candidate.py D:/npudaomang/swin/fast384/export4
$env:PYTHONPATH=(Get-Location).Path
python fast384/build_plan_384.py swinl-384.plan --model D:/npudaomang/swin/fast384/export4/swinl-fp16-lower.onnx --fast-v4
```

仓库内代码已独立重建计划并确认字节一致：
MD5 `d7fda40af91b1e7e4e0a2ef54517c16d`，459572610 字节，44 子图，7514 算子。

Android 仓库根目录的快速 APK 构建命令：

```powershell
$env:ANDROID_HOME='C:/Users/27728/AppData/Local/Android/Sdk'
.\gradlew.bat --offline --no-daemon '-Dorg.gradle.jvmargs=-Xmx2g -XX:+UseSerialGC -Dfile.encoding=UTF-8' --max-workers=2 -PswinlFast=true :app:assembleDebug
```

`-PswinlFast=false` 构建修正分类矩阵的 640×480 配置，使用独立模型资产
目录 `src/swinlCorrected/assets`。384 模型、测试输入和哈希位于
`src/swinlFast/assets` 与 `src/swinlFastDebug/assets`，构建时只打包所选配置。

## 边界

384 的优势来自减少空间分辨率，精度会变化。已有暗光样本中，与同尺寸 FP32
参考分类一致率约 99.90%；但与原 640×480 FP32 结果放大对齐仅约 83.70%。
前者衡量转换误差，后者反映分辨率改变带来的预测变化，都不是数据集 mIoU。

该速度测量只包括预处理、模型和解码，没有摄像头、深度、VINS、建图。
1.5 秒导航新鲜度检查没有放宽，2 秒模型耗时仍不能据此宣称实时导航可用。
