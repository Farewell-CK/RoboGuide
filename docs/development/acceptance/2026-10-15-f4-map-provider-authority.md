# F4 typed-map producer、原始声明与 mTLS 验收

日期：2026-10-15。状态：当前 producer 切片通过，F4 继续实施。实际进程同处一台主机，
三条入口使用真实 mTLS；没有设备、模拟器、模型调用或实验。软件证书不证明硬件身份。

## 实现合同

Node config v0.9 / Node Contract v0.8 / catalog v0.11 显式协商 provider-bound typed-map。
旧 config v0.8 / Contract v0.7 的 Memory continuity 保持兼容。Map v0.2 冻结原 build Task /
attempt / LocalSystem / provider / content schema；HTTP publication envelope 的当前 publish
attempt 与 Node/session context 独立核对。Headers 和 body context 均不是 credentials。

Node 在 Local How 前核对 provider/operation owner，冻结原 output 后由实际 Node 证书上传 CAS。
Controller catalog 在真实 writer guard 内重新验证证书、完整 upload 归属、当前 session/provider、
原 completed build 与当前 Control-bound publish attempt。取消、hold、release、terminal 或错
owner/selector/anchor/slot 的当前 attempt 不能发布。Typed-map discovery/policy projection 使用
v0.2 的真实注册 provider/content schema；历史 v0.1 只保留原兼容 catalog 名，不补 provider。

Runtime 的两个 dispatch 入口共用原始注册快照函数，保留实际 Node Contract、已接受 session
和 provider 声明。这份内部证据不进入 MI 语义参数。重试/restore 不从新注册补旧字段；发送
前来源 session/contract/声明变化时保留 pending/ownership。发布还要求 build、publish 两份
原声明均与当前 provider 相符。单独检查当前注册不足以证明过去生产者，因此旧缺口保持 unavailable。

Wrapper v33 / inner v21 / map codec v24 / Node journal v8 是当前版本。v8 保留 v7 物理 table
layout 和 readonly legacy views，通过 `user_version` 与持久 marker 双重 fence 拒绝旧 v7。
v32/v7 读取升级保留原 history、缺失证据和预算，不自动获得新 provider 权限。

## 当前实际结果

`/tmp/rg-spatial-mtls-20261015-d/result.json` passed。Node ID 为部署生成的任意软件身份；
真实 Node enrollment 不增加虚构设备能力。实际为 1 个 CPU Node、1 个 Native worker、
1 次 Node-owned CAS publication 和 1 个独立 verifier 进程；设备/模型/模拟器均为 0。
Node/Operator/Artifact 三条连接均验证实际证书；独立 verifier 以单独证书主体
`independent-file-reader` 提交证据，Artifact 读取仅有当前 map grant。

Controller binary SHA-256：
`411e7bbccc8562a52d11709485a8c12da74100703b639845ca72a9ed32e1a36f`。
Node binary SHA-256：
`42ca3d95245cfb510f154502f4d9bd388dc4a35cafafd3906158d0ed02120e0a`。
Spatial package source digest：
`sha256:a0d6ba0ee208009b1f6f74275dd26d1a0b152cf5e0cb914389bb8b4cc0ccb780`，
与实际 computed manifest 相同。原 Node TOML digest 为
`sha256:5cfef2a3d1f96c314cc8e9344cbb8cfabe0c1e2d58e6e4df2af9ab4acb362547`。
独立 Python 3.11/Open3D 0.19.0/NumPy 2.4.6/SciPy 1.17.1 沿用先前文件验收；三个核心 native
binary digest 仍在产物中。它们与记录的六个 checker module hashes 均不声称完整传递依赖闭包。

来源是原 Open3D DemoICP PCD 的两个实际文件；独立 verifier 重新读取源文件与 CAS 下载的
全部 bytes，不 import provider。产物 67352 bytes、2129 个观测点、19 个分块，SHA-256 为
`15817c6ccca6e5e987cecd82785431e16a86da10987c71c2dd2c4d15b6ca5798`。Root frame、anchor、
source hashes、实际 PLY 点数/有限坐标/bounds、transforms、原质量策略和 provider 声明均核对。
它证明文件来源/产物完整性；物理真值仍为 unavailable。几何精度证据沿用独立文件变换验收。

Runtime 原 source/publication 声明保存在 `dispatch-declarations.json`。实际重启 Controller 与
Node 后，原 worker-started 和两份 Local How admission 字节不变，没有第二次计算。Mission
在独立 evidence 到达前保持未完成；之后才 Completed 并释放 reservation。重复 evidence 返回
原 receipt。损坏一字节 bundle 被独立 verifier 拒绝；错误 worker PID 只得到 UNKNOWN。

| 实际 mTLS 请求 | HTTP 结果 | 原因 |
| --- | --- | --- |
| 伪造另一个 Node header | 403 | 与实际证书 Node 不同 |
| 旧/不存在 session | 403 | 当前 accepted route 不匹配 |
| grant 中存在但 Node 未声明的 provider | 403 | 实际 registration 无此 provider |
| 不匹配 content schema | 403 | provider owner/kind/schema/media 不匹配 |
| 旧 v0.1 map 新写入 | 403 | 缺少实际 producer/provider |
| Artifact viewer 写入 | 403 | 证书为只读 |

完整响应保留在 `admission-rejections.json`；catalog 最终仍只有原记录。
`owned-process-exits.json` 保留本次所有受控 Popen 的实际 wait/returncode，原进程均已回收。

## 实际旧二进制拒绝

`/tmp/rg-spatial-mtls-20261015-d/upgrade-gates.json` passed。实际旧 Node SHA 为
`351b2c7d8ae55861121f39eb799ccc24e06bb1119789f6ea661861db8595dce0`，旧 Controller SHA 为
`8c029b96b39a51efffc91f28c2f7d1a9a3a6fe8d611b21d8369ffb9e2d37ef46`。

旧 v7 Node 使用可解析的 v0.8 配置，分别因 version 8 和 header 回写为 7 后仍存在的 marker 8
被实际拒绝；不是用新配置解析失败代替 journal fence。原 Node authority 行保持不变。
旧 v32 Controller 在原 map DB 中先被未知 codec v24 拒绝。另起真实当前 Controller，提交一个
空 Memory policy registry，确认 inventory=[]、events=0、wrapper=v33，再停止进程；旧 Controller
被 wrapper v33 拒绝。这份零事件数据库也没有被改写。原 DB 的 events/checkpoint/command journal
逐类型有序行 digest 在检查前后相同。没有新 Node facts、设备或模型参与此检查。

## 复现与边界

```sh
uv run python tools/quality/check_spatial_node_processes.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --profile /tmp/rg-spatial-local-20261015-c/profile.json \
  --source-root /tmp/roboguide-spatial-provider-inputs-20261015/extract/DemoICPPointClouds \
  --output /tmp/rg-spatial-mtls-NEW --mtls
uv run python tools/quality/check_spatial_upgrade_gates.py \
  --directory /tmp/rg-spatial-mtls-NEW \
  --old-node /tmp/roboguide-node-f4-journal-v7-reference \
  --old-controller /tmp/roboguide-controller-f4-wrapper-v32-reference \
  --output /tmp/rg-spatial-mtls-NEW/upgrade-gates.json
uv run python tools/quality/check_spatial_map_contracts.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --directory /tmp/rg-spatial-mtls-NEW --output /tmp/rg-spatial-mtls-NEW/schema-validation.json
```

独立 jsonschema/4.26.0 使用本地固定 registry 验证实际 catalog manifest，以及由真实 attempt
重建的 publication envelope；没有声称捕获 TLS 明文 body。两个 schema 与八个结构拒绝案例
通过，源码/schema hashes 在 receipt 中。当前 Rust 全 workspace 806 项通过、2 项扩展检查
未运行；完整 Clippy/private Rustdoc warnings denied 及受影响模块后续检查通过。
最终正式入口修正后完整 workspace 再次 806 项通过、2 ignored；完整 Python 2486 项通过，
维护路径 strict mypy 219 sources、Ruff/format 352 files、函数文档与 whitespace 检查通过。
全根 evaluation typing 的既有跨包问题继续由 F10 修复，未用上述维护路径检查冒充全根 mypy。

初次 `-a` 因检查器错误选择 tenant scope 导致全局管理请求拒绝；`-b` 是早一版代码通过记录。
`-c` 实际发现正式 Task dispatch 漏接原始声明，发布被拒绝。已将两条入口统一，并由 `-d`
验证正式业务路径。旧记录与首次 upgrade probe 的更早 codec 拒绝日志均保留，没有改成成功。

完整 Group grant/撤销 ACK、pre-Execute preparation、typed-map 全 manifest 内容策略绑定、
handoff、active-map/GC 以及 F5–F10 继续实施。当前登记的 producer namespace 不代表任意接收
provider 已导入或可用；map/Local How completion 不证明当前定位或设备 safe-state。跨主机运行、
设备停止/交接/硬实时/物理连续性保持各自待验证状态。本切片不关闭 F4 或整体 SoftwareComplete。
