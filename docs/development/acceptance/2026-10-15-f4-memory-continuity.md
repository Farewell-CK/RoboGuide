# F4 当前 Memory 连续性切片验收

状态：当前切片软件验收通过。F4 整体仍在实施：完整 tenant/Group 内容授权、active-map、
tombstone/GC/revocation 和空间计算，以及 F5–F10 仍为必需工作，不能由该记录关闭。

## 当前软件路径

Controller 原 import → complete checkpoint/command journal → exact Node/epoch route → Node v7
journal 原完整 tuple、实际 provider instance、workflow/connection digest → actual provider import。
副作用前的确定性校验可拒绝；工作流已经开始后的错误保留 Unknown。原 consumer/selector 的
Unknown 同时 fence replacement import ID、provider、Node 与 attempt，不能换身份重做。

管理员显式 query → 原不可续期 deadline/count/generation → quorum/epoch 后投递 → Node 先持久
接纳 → 原实例固定 readonly GET → 完整 durable 原结果校验 → immutable receipt/元数据恢复。
query 不复制 payload、不重复 import、不重跑同一个 query ID，也不完成 Task 或释放资源。
迟到或已改变 ownership 的结果只保留历史。原 tuple/digest、结果、预算和 wrapper 在 restore
时复核。Imported 是历史证据，不能授予当前可用性租约。

新配置显式协商 Node Contract v0.7 / config v0.8（IDL catalog v0.10）；旧端无法通过忽略新
protobuf frame 获得支持。Controller wrapper v31、Memory event codec v22 和 Node journal v7
分别保持回退边界。v7 使用版本命名的权威表和只读 legacy views；旧 v6 程序无法写入这些 views。

## 实际进程与文件证据

命令：

```bash
uv run python tools/quality/check_memory_processes.py \
  --output-directory /tmp/roboguide-f4-memory-processes-20261015-final \
  --old-node-binary /tmp/roboguide-node-f3-journal-v6-reference
```

passed=true。运行的是 shipped 文件 Memory backend、Controller、正式 Node 和一个实际文件
消费者 OS 子进程，设备数为 0。Node 从实际 provider 发现/导出并发布真实源码文件 bytes。
故障注入在 bytes 已 fsync 后中止最后 SQL 更新；原 backend row 保持 pending、Node/Controller
import 保持 Unknown。换 import ID 被拒绝。正式 readonly query 校验原实例和当前实际 bytes，
没有第二条 import row、没有重复复制；原 backend SQL row 在 query 后仍为 pending。

对账没有结束消费者进程。Controller 实际重启后原 command、query deadline、次数、完整收据
和 receive time 不变；实际消费者没有重启或重复执行。后续实际消费导入的文件，并由另一个
OS 子进程独立重算后，经 CAS/verifier ingress 才完成 Mission。

实际旧 Node 程序退出码 1，错误为 `cannot modify memory_imports because it is a view`；没有
连接 Controller 或启动旧 workflow。旧程序 SHA-256：
`5e669c8fe21937270499ac472d7b06d665f8dc159360209a74c4e0d218a04949`。

本次 Controller SHA-256：
`8f8bb1394b024b90a855e98692c492e8eb77d4cdd00f2176836065b50c90dd61`；Node SHA-256：
`d20db02493cb56dc66e1aacaa759a12843cc72fc42cf3c51c7bdb0dc1d74a77f`。
运行配置 digest：`sha256:8125a227dce777fc1fa4ad02f6524b76beddc1086e726f91d5465edac262a5be`。
原件保留在目录中的 result、日志、Controller SQL、Node journal、backend SQL 和实际 CAS。

首轮 `/tmp/roboguide-f4-memory-processes-20261015-a` 因 authored manifest 使用错误的 owner
tag 失败；Node 拒绝发现，未制造执行或成功。修正配置为正式 `owner` tag 后通过，未放宽 Core。
`...-b`、`...-reviewed` 和 `...-slot-fence` 保留前一源码快照；上面 final 目录对应当前切片。

同一 Controller 二进制的静态三成员 mTLS 回归：
`/tmp/roboguide-f4-replica-check-20261015-slot-fence` passed=true、wrapper v31，校验 minority、
旧 epoch 路由关闭、Leader death 保留 Unknown/ownership/policy/journals、33 行元数据前缀重新
汇合、单机降级拒绝及 majority loss fence。该工具的 LocalFixture 仅证明协议矩阵，不证明
物理执行连续性；动态成员、证书升级和完整内容恢复继续由 F5/F10 验收。

## 自动检查与边界

完整 Rust workspace 当前 790 passed、2 ignored；Integration Server 208、Node Service 118、
Integration 31、State 79。后续 slot/provider fence 修正重新通过对应 Node/Application suite、
workspace Clippy 与当前二进制实际进程检查。原 791 项中的一项仅测试已删除的旧错误分类函数，
已随该废弃函数删除；导入后的实际错误由 Unknown 进程/日志路径验证。两项 ignored 是已有的扩展 ownership/replay 检查，
F10 仍须做更广的软件 ownership 验收，不能将它们归为仅等待设备。

完整 Python 2463 项通过；strict mypy 197 source files、Ruff 206 files、函数文档、Rust
fmt/Clippy/Rustdoc 通过。新增三份 JSON schema 结构有效，实际 v0.8 Node 配置与真实 provider
result 通过 schema 校验。SQL rollback、原 tuple 改写、query interruption/deadline/replay、
替换 provider、迟到结果与旧 wrapper/codec 负向检查均有软件回归。另修正已有 attestation 负测的随机不稳定输入：
把首字节置零可能没有改动原随机签名；现在固定 XOR 翻转一位，并断言输入确实不同。

真实停止、设备身份、硬实时与物理连续性保持 HardwareEvidencePending。数据面当前仍有
startup grant，完整分布式 Group/tenant 内容授权和生命周期是后续必须完成的 F4 工作。
