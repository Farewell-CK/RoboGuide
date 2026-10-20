# 有界观测维护代码集成验收

从整改主线 `8baab4a1925fea840734bf2436971a857594cce1` 三方合入固定头
`983c8810b91ccbd5634dd9b3b7a9901c510d38cc`，保留四条原提交和人类作者。
原工作树、未跟踪实验文件与结果不变；没有运行模型、模拟器、E1 或设备。

## 合入行为与边界

- Local How v0.13 在原 acquisition 预算内提供显式模型选择的有界 yaw 扫描；最多
  32 步、90 度、1.5 rad/s，不续预算。只使用经检查的原控制类、原多 agent 参数前缀
  和原 `get_num_actions` 完整向量编码，包括 EmptySpace；缺少接口拒绝安装。
- 扫描只作用于当前 endpoint，结束时移除其临时 skill；扫描完成不代表检测、定位或目标满足。
- 默认关闭的感知诊断仅读取已有缓存，限制 camera/pixel/record 数量。own-agent、派生
  world union 与 unavailable official truth 分开记录，不额外调用 sensor/render/predicate。
- evaluation 的只读部署校验识别 v0.13 的完整扫描声明，旧 profile 不获得新支持。

Controller wrapper v44、Bridge inner v23、event codec v28、Node journal v13、Node Contract
v0.12/config v0.13/catalog v0.15 均保持原义。Memory provider 进程和 Task import 修复保留。
这次合并没有 Rust 或该 provider 的行为变化，沿用上一工作包对应的实际进程验收身份。

## 质量证据

日志前缀为 `/tmp/rg-adapter-983c-`。实际部署解释器为
`/home/duchaoke/miniconda3/envs/habitat/bin/python`；
`PYTHONPATH=integrations/habitat-local-eaios <python> -m habitat_local_eaios.runtime_check`
通过 canonical parsing、SQLite round trip 和 loopback HTTP，零 Provider/模拟器调用。
该检查不加载世界，不证明扫描物理效果或仿真 readiness。

全量 Python 回归 2,769 passed / 0 skipped；Ruff/format、主包 269 files 与 evaluation
78 files strict mypy、函数文档、diff 和严格文档构建均通过。
测试中的 vendor/action fixtures 只验证编码及边界，不当作真实设备或
当前实验 outcome。F0/F4–F10 和总体 SoftwareComplete 仍按完整整改计划验收。
