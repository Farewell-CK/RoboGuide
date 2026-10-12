# 发布技术准备审计（2026-10-12）

O8 只完成技术准备。项目许可证、发布范围和历史凭据处置仍需负责人决定。本记录不表示
开源发布批准、许可证兼容性结论或生产网络认证已完成。

## 来源与扫描

源码基线为 `dev@9ed350c305afe86bf1339fdccc4c181859f43ee0`，本地 55 个 refs 可达的
561 个 commits。Gitleaks 8.30.1 使用内置规则、全部历史、100% redact 和
`--ignore-gitleaks-allow`；另扫描由 `git archive HEAD` 创建的 tracked snapshot。
工具来自[官方 release](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)，归档大小与
release checksum 一致，SHA-256 为
`551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`。

| 范围 | 自动提示 | 核验状态 |
| --- | --- | --- |
| 当前 tracked HEAD | 0 | 本规则集未发现凭据；不能证明所有安全性质 |
| 本地所有 refs 可达历史 | 23 | 22 个 Provider response cache 标识；1 个历史硬编码凭据 fallback，需负责人处置 |

22 个 cache 提示均来自 `prompt_cache_key`，实际值符合 UUID 形状；没有据此证明 API
认证凭据泄漏。另一条旧功能分支的编译配置把具名 API key 的非 placeholder 值作为
默认值写入生成常量；当前 dev 不包含该文件，凭据当前有效性没有测试。
**这条历史风险未关闭，不能把 HEAD 的 0 提示转换成整个仓库安全通过。**

原始自动报告仅保留 100% 脱敏结果，人工核验记录不含值，保存于本地审计目录
`o8-release-preparation-2026-10-12/`。详细定位和 refs 在该受限目录中，未把凭据复制到
此文档。扫描不覆盖不可达对象、未获取的远端 refs、ignored/untracked 本地配置、外部
Conda/vendor 环境、模型、数据集与压缩包内部；decode depth 为 5，archive traversal 关闭。
后续凭据失效/替换需核对实际持有人和服务；此工作没有验证凭据或重写 Git 历史。

## 锁定依赖的许可证元数据

`cargo metadata --locked` 覆盖 258 个 resolved packages，其中 14 个 workspace packages
及 244 个外部包。244 个外部包都声明了 license 或 license_file。声明包含 OR/AND
组合、Unicode、ISC、BSD、Zlib、LLVM exception、CDLA 及一个 LGPL 可选分支，不能压缩
成“依赖都是 MIT”。4 个 `MIT/Apache-2.0`、1 个 `Apache-2.0 / MIT` 和 2 个 `Unlicense/MIT`
使用旧分隔写法，后续 policy checker 需要明确处理。

`uv.lock` 的 16 个 packages 包含 1 个本项目和 15 个外部包。15 个已安装包的 metadata
直接读取，其中包括本项目；未安装的 colorama 从 lock 指定 wheel 获取并验证 SHA，
只读 METADATA/LICENSE，没有安装它。15 个外部包均取得许可证元数据。

仓库根目录与 14 个 Rust workspace packages 尚未声明项目 LICENSE；本项目 Python
metadata 也没有许可证声明。许可证选择和逐项义务核验继续待定。当前清单记录实际
name/version/source/license expression/license file，未作 SPDX 规范化、法律兼容性判断
或第三方通知文件的完整性认证。外部 simulator、vendor 源码、预训练模型、数据集和
动态下载资产需按实际发布范围另行盘点。

## 文档标识与示例

Actor takeover 保留 ADR-0070。原 relocation registration/reset evidence 文档改为
[ADR-0078](../decisions/0078-relocation-registration-and-start-evidence.md)，保留 accepted
decision 和历史编号说明；旧版本仍能按原 commit 读取。当前 78 个 ADR 标题与文件名
一致，无重复编号；repo 内没有遗留旧 relocation 路径引用。

已验证现有声明式示例的 `roboguide-node --validate`：只做 offline compile，输出
`controller_contacted=false`、`runtime_probes_executed=false` 和
`hardware_probes_executed=false`。新建目录上的最小实际 loopback quickstart（one wave，
diagnostics on/off）共 **4/4 synthetic Mission durable completed**。

可复现的最小入口：

```bash
uv sync --locked
cargo build -p integration-server -p real-node-smoke -p roboguide-node --locked
target/debug/roboguide-node --validate scenarios/extension-conformance-v0.1/node.toml
uv run python tools/quality/measure_controller_pipeline.py \
  --output /tmp/roboguide-quickstart-new --repeats 1 --waves 1
```

入口不运行 MI、Provider、Habitat 或设备，结果仅证明该合成路径。输出目录必须不存在，
原始结果、进程日志、diagnostic windows 和 SQLite 保留在该目录。

## 当前网络与认证边界

Controller gRPC、Control HTTP 和 Artifact HTTP 默认 loopback。当前 composition root
没有配置 TLS/mTLS 或 HTTP caller authentication；Node identity/session/sequence/owner
验证是协议与执行归属校验，不是外部调用者的密码学身份认证。可显式绑定其他地址，
本地可执行示例因此以可信主机/网络为部署前提；它不证明公网接入就绪。

MI 示例通过环境变量读取 API key。具体 Local EAIOS 的 endpoints、workflow 和凭据
属于启动配置；Mission 参数没有配置命令或改变路由的权力。真实安全停止与硬件
能力仍属于 Local EAIOS，RoboGuide 的 lease expiry、cancel receipt、diagnostic 或
Mission isolation 都不能替代物理停止证据。

## 复现扫描与清单

在安装并校验同版本 Gitleaks 后，对本地 repo 运行：

```bash
gitleaks git --log-opts=--all --redact=100 --ignore-gitleaks-allow \
  --report-format=json --report-path=/tmp/roboguide-history-redacted.json .
cargo metadata --format-version 1 --locked > /tmp/roboguide-cargo-metadata.json
```

Gitleaks exit 1 表示提示需要核验，本轮历史扫描为此状态；exit 0 才表示该规则集未发现
提示。不能通过忽略所有旧分支、silent allowlist 或删除报告使历史风险看似消失。
Python 许可证清单按 lock 版本与 installed METADATA / SHA-verified wheel 对齐；不能
用开发机任意已安装包列表代替锁定依赖清单。
