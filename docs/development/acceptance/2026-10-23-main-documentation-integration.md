# 同分支同步 main 架构与网站修复

唯一开发分支仍为 `codex/nonrelease-remediation-closure`。`a0e74d0` 的
[完整 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39827808789)和
[文档 CI](https://github.com/Farewell-CK/RoboGuide/actions/runs/39827808737)均成功。
本次将 `origin/main@ad4fed6c00ef41d0da12f757375a7632633a982c` 合入该开发分支，
没有新建分支或将整改提前合回 main。

## 实际合并范围

main 新提交只涉及 V2 中英文整理、历史正文保留、源码绑定的 Mermaid/SVG 图册、
网站图像/无脚本移动端阅读及有界浏览器检查，不修改运行期权威代码。README 的新图册
保留，实施拓扑保留本轮 wrapper v52/storage v5/recovery v0.16，网站内容基线更新为
实际已提交 `a0e74d0`。V2 主文同时区分本开发分支的已验证实现与尚未合回的远端 main，
原 10 月 22 日图册/审查证据不改写。

实际严格构建、页面检查、9 项 website 单元测试、Ruff/format、strict mypy 和函数
文档检查通过。Playwright 使用实际已安装 Chromium、独立 loopback 静态服务器和
受限第三方请求，验证中英文图册、390/768/1440 三种宽度、两种主题、模块图、搜索/
导航/键盘焦点及关闭 JavaScript 的移动端图像；原 stdout、截图、服务器原 wait 和
各次构建记录保存在 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`
的 `source-gc-main-merge-*` 中。具体通过结论以完成后的原记录和此合并的 CI 为准。

此合并不运行实验、模型、模拟器、Node 或设备，不更新发布配置或变更生产分支设置。
全部非发布计划继续按同一开发分支实施，Raft/在线 producer 和其余 N1–N7 门禁保留。
