# RoboGuide · NAVI 室内导航

NAVI 以 Flutter 模块接入 RoboGuide，在同一安装包内提供地图管理、路线录制、视觉定位、
路线跟踪、中文语音与字幕，以及实验性的室内障碍提醒。

本网站说明 NAVI 分支的使用与源码结构。它与主项目的架构文档站分别维护。
网站可以构建、应用可以运行、服务器可以访问，是需要分别验证的三件事。

## 从这里开始

- [安装与首次使用](getting-started.md)：进入 NAVI、填写配置和登录。
- [导航与障碍提醒](navigation.md)：导航流程与功能边界。
- [后端与网络](deployment.md)：视觉服务、数据库和网络的关系。
- [同事测试流程](testing.md)：测试顺序与问题记录。

## 源码在哪里

| 目录 | 内容 |
| --- | --- |
| navi_indoor/ | Flutter 手机端、地图、导航、语音字幕与诊断 |
| app/src/main/java/com/seaway/guideassistant/navindoor/ | Android 宿主与 Flutter 入口 |
| services/navi_vision/ | DINOv2 CPU/GPU 服务与障碍接口转发 |
| services/navi_obstacle/ | Swin-L / Mask2Former 室内障碍服务 |
| docs/navi/ | 整合、构建与历史验证记录 |
| website/ | 本文档站源码与构建检查 |

[打开 NAVI 源码分支](https://github.com/Farewell-CK/RoboGuide/tree/feat/navigation-data-archive)
 · [查看整合合并记录 PR #7](https://github.com/Farewell-CK/RoboGuide/pull/7)

## 版本与验证

本分支手机模块的版本为 1.0.18+19。模型权重、APK、登录凭据、运行密钥和地图数据
不随这套网站源码分发。历史测试记录带有各自的日期，不能作为新设备或新网络的测试结果。

网站的 [build-info.json](build-info.json) 记录实际构建提交、分支和工作区状态。
本地未提交的预览会明确标为 dirty，不代表线上发布完成。
