# NAVI 室内导航模块

此目录是嵌入 RoboGuide Android 宿主的 Flutter 模块。完整接入方式、目录、服务配置和验证边界见 [整合说明](../docs/navi/整合说明.zh-CN.md)。

不要修改生成的 `.android/`；计步和图像压缩的原生实现位于宿主 `app/.../navindoor/NaviIndoorActivity.kt`。运行 `tools/build_navi_indoor.sh` 或 `.ps1` 生成宿主依赖的 AAR，最终只需安装宿主 APK。
