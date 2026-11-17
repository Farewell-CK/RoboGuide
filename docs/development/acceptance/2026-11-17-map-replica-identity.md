# 地图导入副本登记的 Node 身份头修复

日期：2026-11-17，Asia/Shanghai。

## 结论与源码

VERIFIED：旧客户端的地图副本上报缺失 Node/Session 头，已通过实际 HTTP 请求捕获复现。
修复后上报携带接收方 Node 和其已接受会话；非管理员 mTLS writer 对获授权地图可正常
登记 staged、imported、verified 和 rejected。证书、头部和正文的 Node 必须一致。

基线：`c17d07f901555ff24c68ee3e3f0eb5fa5e23fcc9`。
分支：`codex/fix-map-replica-identity`，独立于此前取消结案修复。

- Windows：`C:/Users/33457/.codex/worktrees/rg-map-replica-identity/rg-task-cancellation-reconciliation`。
- ThinkPad：`/home/duchaoke/rg-lab/wt-map-replica-identity-20261117`。
- ThinkPad 证据：`/home/duchaoke/rg-lab/map-replica-identity-evidence-20261117/`。
- Windows 证据副本：`C:/workspace/DEAIOS/lab/rg-map-replica-identity-20261117/`。

## 根因和改动

CAS `authorization.rs::authorize_head` 对地图写入检查请求头身份与证书 Node 是否一致，
`authorize_body` 再检查正文中接收副本的 Node。地图的原 producer 可以是另一个 Node。
HTTP 解析器要求 `X-RoboGuide-Node-Id` 与非空 `X-RoboGuide-Session-Id` 成对存在；
只补 Node 头会从 403 变成 400。

`core/node-service/src/artifact/client.rs::record_replica` 原来仅发送 JSON。
本次为它及 `ArtifactStager::record_replica` 增加 session 参数，显式发送接收方的两个头。
`engine/execution.rs::record_input_completion` 从原进程内的 publication_session 读取
实际已接受的 Node Protocol 会话，传给 staged/imported/verified 上报；不从 manifest、
本地工作流响应或请求参数推断会话。会话缺失时保留未完成的结算，不发送副本写入。

CAS 生产授权代码、公开 HTTP 格式、证书映射、provider/map grants 和副本状态机均未修改。
原 no-replay/finalization-only 路径及本地字节复核保持不变。rejected 本来就是服务端支持
的状态，本次用独立 revision 验证；不会允许已 Verified 的同一副本退回 Rejected。
Verified 副本记录不等同于强定位证据或物理目标达成。

## 验证证据

全部编译和测试在 ThinkPad 独立工作树运行，使用 Rust 1.99.1、locked 依赖、最多两个
编译任务和 nice 10。未使用 WSL 验证。旧代码配新增客户端请求测试时 exit 101，捕获
的 POST 不含两个身份头；补丁后该测试通过。为适配新增 Rust 参数，修复后的测试调用
显式传入 receiver-session，其余请求和断言保持一致。

| 检查 | 结果 |
| --- | --- |
| 旧客户端请求复现 | 1 失败，exit 101，确实缺头 |
| 修复后客户端请求捕获 | 通过，staged/imported/verified 使用接收方身份 |
| 引擎精确 finalization 重试 | 通过，缺字节/缺会话时零副本写入，有会话后 staged/imported 完成 |
| Artifact HTTP 回归 | 34 通过 |
| Node service all-targets | 151 单元测试 + 1 example 测试通过 |
| workspace Clippy，all-targets/all-features，warnings denied | exit 0 |
| cargo fmt --all -- --check | exit 0 |
| git diff --check | exit 0 |

正式相关回归合计 186 项通过，上述两个定向测试已包含在该计数中。

新增 mTLS 回归运行真实生产 Artifact listener、短期独立测试 PKI、真实 TLS 握手和私有
SQLite。明确授予 receiver writer 身份而不是 Admin，保留 dog-a producer / dog-b receiver
的跨节点差异。四种合法状态均返回 202；每种状态分别检查下列七种拒绝场景：

- 缺少两个身份头：403。
- 只带 Node 或只带 Session：400。
- 头部冒用 producer Node 或正文冒用其他 Node：403。
- 证书未获该地图授权：403。
- 只有 viewer 权限：403。

合计 28 个负例均未增加业务事件。重新 replay 私有 SQLite 后，成功 revision 保持
Verified，独立拒收 revision 保持 Rejected。这里的会话头传递及解析测试不扩展既有地图
副本的授权语义，也不声称新增了服务端会话新鲜度规则。

现网 Controller/Mission Service 原 PID 444582/444591 在验证结束时保持运行；
`/home/duchaoke/workspace/RoboGuide` 仍为干净 main。完整源文件摘要见证据目录中的
`source-sha256.txt`；Windows 工作树按 Git LF 内容核对到同一份实测源码。

## 部署边界

本轮未部署二进制、重启或修改现网 Controller/Mission Service，也未操作真实接收方
Node、重跑地图导入、调用现场副本登记或重试原 Mission。现网主 checkout 保持干净。
此次运行时修复属于接收方 roboguide-node；CAS 生产源码无需更改。现场任务收尾应在
单独部署并核实原 journal/finalization 状态后，通过既有明确授权的恢复流程完成。

本轮测试是源码、传输、认证和持久化验证，不是两台 Thor 地图共享的现场验收。
