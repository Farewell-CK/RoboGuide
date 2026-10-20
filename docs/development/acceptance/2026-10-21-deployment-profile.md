# 可执行部署 profile 与零 Node 软件验收

状态：当前软件输入合同和实际配置门禁通过，其他阶段继续。
基线 `5e7c224`；设计见 [ADR-0129](../../decisions/0129-executable-deployment-profile.md)，
闭合输入见 [profile v0.1](../../../contracts/deployment/profile-v0.1/README.md)。

## 当前实现

`tools/deployment/deployment_profile.py` 接受显式组件/共址进程、主机/控制域、exact tenant、
原 binary/配置、store owner、协议/信任连接、故障 RTO/RPO 目标与退出证据。没有预置
Node、设备、Capability 或 Actor placement。原字节、binary 的只读 application schema、
目录/inode、TLS 公钥材料/真实 key pair/实际 leaf 和固定 GET 被检查；私钥内容不记录。
外部原配置按其 digest 保留，检查不会向 Controller 提交新事实。

不同 host/domain 的 plaintext、跨 host/domain Unix、路径/摘要变化、store alias/重叠、
不一致的共址声明、缺失的合同/criteria 引用及过期检查预算均拒绝。远程 store 声明不会
从 inspector 的同名本地路径推断，保持 `remote-store-not-inspected`。网络观察记录真实
inspector host，不声称运行了声明的源进程。gRPC 只做 TLS/ALPN，不 Register 或证明 wire
语义。输入 count/bytes、响应 size、subprocess/network timeout 与总检查预算有界。

不可覆盖的私有 receipt 保存声明、原 pins、实际本地观察与 pending criteria。证据文件
完整性不是语义通过；健康不是执行 readiness；RTO/RPO 仍是目标。检查器不授予执行/
内容/物理安全权，也不自动宣布全项目 SoftwareComplete。

## 实际进程

`/tmp/rg-deployment-zero-node-20261021-d/RESULT.json`：
SHA-256 `77ef0821adeaed14032fd4941137bde6b555a8652b8ccb90839a9a952d26e3e5`。

一个真实 integration-server 进程提供 Operator、Artifact 和 Node Protocol 三个实际
认证 listener。实际四次探测为 Operator health、inventory、Artifact health 与 Node TLS。
初末 inventory 的 Node 列表均为空；Node Service、设备、模型、模拟器均未启动，未调用
身份注册、Mission 创建或任何 Execute。完整启动 argv、显式环境/空 Node registry 和
authorization 原文件也固定在 profile inputs 中，未把静态清单当成设备事实。

真实 binary 返回 `roboguide.controller-checkpoint/v45`，SHA-256
`87e51b689be71f5cd7f70caa78179f8ddf9930fc57801dddf647999f25c7abe0`。
profile digest `sha256:16e38f8b10e317b214a829d89cdde0d79ac8bf410d3bd6410aff98ee6ff76c61`。
实际修改 leaf pin、配置 pin、SQL 硬链接和原 30 秒检查预算收紧到 1ms 的四次拒绝均保留；
失败不发布 receipt。原 owned Controller 和所有同步检查子进程均已结束。

首轮 helper 对整个 inventory 做相等比较，误将合法变化的观察时间视为故障，证据保留在
`rg-deployment-zero-node-20261021-a`。现在只断言原 Node 列表仍为空，并保留前后完整观察；
没有放松产品身份、writer 或 TLS 门禁。

## CI 与验证

21 项当前模块检查覆盖原文件/alias/边界/预算元数据、缺失硬件、远程不可观察和原证据的
完整性与通过状态分离。CI 增加相同实际 zero-Node gate、private Rustdoc，并将 deployment/
observability/recovery/Memory/Spatial/Habitat/Eval 纳入维护模块的格式、类型、函数文档和
测试检查。独立 Python 3.9 interpreter 只跑标准库 bridge/parser/SQLite/HTTP 兼容路径，
不安装或启动 Habitat。实际本机 Python 3.9 `-S` 门禁为 PASS，模型调用为零。

既有 2785 项均通过全量回归，当前新模块 21 项另行全部通过；最新 CI 共收集 2806 项。
主严格 mypy 290 files、Spatial 13 files、observability 3 files 和完整格式/函数文档检查
通过。完整套件首轮期间补的三项 deployment 边界已在最新 21 项中覆盖，不把早期 18 项
结果标成最后源码的 21 项。Rust 源码及原 binary 未改动。

```sh
.venv/bin/python tools/quality/check_deployment_profile_processes.py --output /tmp/unique-deployment
.venv/bin/python tools/deployment/deployment_profile.py --profile /absolute/actual-profile.json \
  --output /absolute/new-inspection.json --probe
```

原 `/tmp` profile 的监听现已回收，其 receipt 是当次观察，不能声称当前还在线。实际生产
地址、独立故障域、全局内容、HA、长期 SLO、硬件与其余整改仍须各自验收。完整物理 MVP
的历史 Draft/候选状态不被软件配置冻结改变；没有发布或上线操作。
