# F4 完整 typed-map 内容策略验收

日期：2026-10-15。当前内容策略切片通过，F4 继续实施。设备、模型、模拟器及实验均为零。

Memory authorization v0.2 保留通用 manifest digest，并显式增加原 typed-map 的完整 metadata
digest；frame、anchor、format、resolution、parent、来源身份和时间均被绑定。Control 自行
构造通用 projection 并核对两份摘要；只有 projection 的通用授权不能绕过 typed binding。
历史 v0.1 无完整依据时拒绝 typed-map 访问。显式升级可增加 binding；一旦固定便不能更换、
删除或降级。完整 tenant/revocation/history 仍进入同一 authority transaction。

Controller wrapper v34 显式读取 v33，并拒绝将新策略藏入旧 wrapper；共享 schema 同时供
replica supervisor 使用。map codec v24、Node journal v8、config v0.9 / Contract v0.8 不变。
这份记录不声称已有完整 consumer preparation 或 Group lease 协议。

实际记录 `/tmp/rg-spatial-mtls-20261015-f/result.json` passed，使用公开 API、正式 Node、真实
mTLS 与独立原文件/CAS verifier。1 个实际 CPU Node、1 个原生计算 worker、1 次 Node-owned
发布、1 个独立 verifier。Controller 和 Node 重启后没有新计算 worker，最终 reservations 空。
产物 67349 bytes、2129 个观测点、19 个块、2 个原始 PCD；SHA-256：
`3c20c05e95e72243792f82d42124bae9a2b943ac135b9acd4469fe5c368f9e94`。
软件文件来源不证明当前设备定位或物理安全。

实际 Controller binary SHA-256：
`5a1551c2c7c25cd2e78a5bb7c5072d75a686ce2d9140ce81958155e79bfb28fd`。
Node binary：`ffc87f7519cc1dd8a7b36f0eba88b053805e94fee9085d9349a642a0b46b99c5`。
Node TOML：`sha256:57ba9bf36d0ad180dd50f02739ea5bff9c921aab3c90b29ac2d5bb9f0cb8519c`。
七个 checker module hashes、原 dispatch 快照、原始 package digest 与完整进程退出记录均保留
在该目录；没有声称完整传递依赖闭包。

`full-map-policy.json` 记录实际旧策略提交 200、无原 map digest 的读取 403、显式升级 200、
授权读取 200、只有上传权限的 Node 读取 403，以及修改 frame 对应 digest、删除 digest、
降级 schema 各 409。拒绝请求未推进当前 revision，重启后保留 revision 2 与原完整摘要。
原六项 publication 403、损坏 CAS 拒绝和错误 worker PID 的 Unknown 检查同时通过。

`upgrade-gates.json` 使用真实归档 Controller v33 binary
`411e7bbccc8562a52d11709485a8c12da74100703b639845ca72a9ed32e1a36f`，分别拒绝原数据库和
零事件/零 Node 的 v34 checkpoint。原 Node v7 双 fence 继续通过，探测前后 authority row
digests 一致。该证据证明软件拒绝降级，不证明生产 HA 或物理连续性。

`schema-validation-full.json` 由独立 Python/jsonschema 4.26.0 校验实际 map、context-derived
publication envelope 和实际 v0.2 策略，包含 8 项 map/envelope negatives 及坏 policy digest。
策略 schema SHA-256：`89b94faa470d347194e4d3b288a55be2807f53e1d7b782f9b8e32a40bc83e904`。

Rust workspace 807 passed、2 个扩展检查 ignored；全 workspace Clippy all targets/features、
private Rustdoc、fmt 通过。四个变更检查器及 TLS/helper 的 Ruff、strict mypy、函数文档通过。
未以局部 typing 代替尚待 F10 修复的全根 typing。初次 `-e` 因脚本未配置同一 map 的通用
projection grant 而正确收到 403，原失败及所有 owned process 退出记录保留；`-f` 是修正后的
完整实际结果。没有写入或运行 experiments。

复现：

```bash
uv run python tools/quality/check_spatial_node_processes.py \
  --python /tmp/roboguide-spatial-provider-venv-20261015/bin/python \
  --profile /tmp/rg-spatial-mtls-20261015-d/profile.json \
  --source-root /tmp/roboguide-spatial-provider-inputs-20261015/extract/DemoICPPointClouds \
  --output /tmp/rg-spatial-mtls-new --mtls
uv run python tools/quality/check_spatial_upgrade_gates.py \
  --directory /tmp/rg-spatial-mtls-new \
  --old-node /tmp/roboguide-node-f4-journal-v7-reference \
  --old-controller /tmp/roboguide-controller-f4-wrapper-v33-reference \
  --output /tmp/rg-spatial-mtls-new/upgrade-gates.json
```

Group grant/撤销 ACK、pre-Execute context、当前后端可用性、handoff/lineage、active-map/GC
和 F5–F10 仍是必需工作。SoftwareComplete 尚未达到；硬件证据独立待验证。
