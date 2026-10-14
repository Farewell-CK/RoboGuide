# 身份、租户与可信证明软件合同 v0.1

状态：已接入当前软件；设备信任根、物理设备身份与跨主机物理连续性待部署证据。
Controller wrapper 为 `roboguide.controller-checkpoint/v29`。旧 wrapper 不能携带新身份、
租户或授权权威。已有复制数据库的版本升级由 F5 的升级合同处理，不能删除旧库绕过 fence。

## 管理与传输权威

`ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS` 支持既有静态映射及
`roboguide.node-identity-registry/v0.1` JSON。生命周期 profile 必须同时配置 Node mTLS
server cert/key/client CA。空 `records` 合法：只有部署后续授权、正式注册和实际工作流才产生
Node 能力事实；登记凭据不生成设备、能力、资源、LocalSystem 或 PhysicalEntity。

管理员通过 `POST /v1/node-identities/commands` 提交 `command_id`、`node_id`、
`expected_revision` 和 `action`。支持 register、prepare、activate、retire_previous、revoke、
renew、reenroll、configure_attestation。相同命令返回原 receipt；同 key 不同内容拒绝。
应用私有 candidate、checkpoint 和 journal 先提交，再向 transport 发布。SQL/quorum 失败
不改变 live registry。tenant/domain 不随 reenroll 改变；已撤销/退役 leaf 不能再次接纳。

轮换的 prepared leaf 尚无传输权限；管理授权激活后，持有新 leaf 私钥的客户端仍须通过
实际 mTLS 和正式注册才能取得 session/lease。旧 leaf 仅在受限 overlap 内有效。
Integration 从 TLS 元数据取实际 leaf digest/notAfter，Control 保存原 process-local session
anchor；每次 ingress、Execute/Cancel/import 路由和 lease 检查都核对当前 registry 和真实 expiry。
心跳不能延长证书寿命。撤销、到期和重启不释放已有 ownership，不证明设备已经停下。

## 稳定主体与租户

Operator 的 `ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP` 可使用
`roboguide.operator-authorization/v0.1`：registry_id、revision、grants；每个 leaf grant 有
principal_id、role、tenant_ids、not_before_ms、expires_at_ms、revoked。
`*` 仅授予 recovery-admin。配置文件原子替换后，HTTP 解析完成、业务授权前重新检查并
持久化 revision；已捕获的旧 principal 无法绕过撤销。旧 grants 保留为 revoked；版本回退、
相同 revision 冲突、leaf 主体改写、撤销后重新激活均拒绝。轮换 leaf 可引用同一稳定 principal。

Mission Service 对应文件 schema 是 `roboguide.mission-authorization/v0.1`，使用 profile_id、
revision、grants；grant 使用单一 tenant_id，`*` 仅授予 admin。相同稳定 principal 的 tenant
不能改写。HTTP writer 锁、授权 revision 与 Request/命令 journal 共用原 SQLite 权威。

`X-RoboGuide-Tenant` 是证书已授予范围内的选择器。用户提交的 role/tenant header 不授予权限。
Mission Service 只转发当前认证 command scope；Controller 在接受计划前持久化不可变 tenant，
并在 Matching、Proposal、Commit、Bind、Recovery、Rebind、continuation 和读取时检查。
跨租户 Node 被排除；新身份 profile 下的 State-free compatibility Commit 拒绝。
没有生命周期 Node registry 的 scoped remote admission 拒绝；原 trusted loopback 的 `local`
单域 profile 保持显式兼容。State/Memory 的完整 scoped 查询和内容授权由 F3/F4 接续验收。

历史无 tenant 的 Request/Mission 必须经管理迁移。Controller 的
`GET /v1/missions/{id}/tenant` 返回原 normalized plan digest；
`POST /v1/tenant-migrations` 接收 command_id、mission_id、tenant_id、source_plan_digest。
Mission Service 同路径接收 request_id、tenant_id、source_digest，digest 来自原 Request JSON。
权限 scope、原内容、已有 owner、重复/冲突、checkpoint/SQL rollback 都被核对；原 receipt
保留管理员与 receive time。迁移不重规划、不调用模型、不派发 Execute、不重新释放资源。
启动生命周期部署前，历史绑定必须与已迁移 tenant 及 Node registry 一致。

## 可信测量

configure_attestation 冻结 policy_id/revision、可信 AK 公钥、精确 SHA-256 PCR 集合、可选
qualified signer/firmware、safe clock、max_age 和 admission 要求。管理员通过
`POST /v1/attestation/challenges` 获取系统随机 nonce 和非续期 deadline，再通过
`POST /v1/attestation/evidence` 提交原 TPMS_ATTEST、签名、签名 scheme 和 challenge_id。

原生 verifier 使用 ring 校验 RSASSA SHA-256、RSA-PSS SHA-256 或 ECDSA P-256 SHA-256，
核对 magic/type、nonce、精确 PCR bank/selection/digest、firmware、clock、启动计数回退、
identity/policy revision、撤销与时效。完整原 bytes、原始 receive time 和验证报告进入
checkpoint；restore 重新验签，不刷新预算或有效期。重复 proof 返回原报告。

此合同接受部署明确固定的 AK 信任锚，不签发硬件证书，不凭 provisioning digest 或软件签名
推导 EK/AK 的制造商来源。报告保留 `HardwareEvidencePending`；真实 TPM/设备信任根与
physical entity 连续性需要独立部署证据。测量 admission 和持续安全观察保持独立，过期报告
可以阻止新 Commit，却不能丢弃已占用执行的认证原始状态事实。

初始支持评估只有出现 identity_unavailable 时使用 assessment v0.3；v0.1/v0.2 继续读取，
不能携带该新语义。失败原因不能写成能力不足，也不向 MI 注入 Node inventory。

## 验证入口

见 [F2 阶段验收](acceptance/2026-10-15-f2.md)。复现真实 Controller mTLS/重启/授权回退：

```bash
cargo build --locked -p integration-server
uv run python tools/quality/check_identity_processes.py --skip-build --output /tmp/identity-new-run
```

该检查会生成本地软件 PKI 并登记一个随机逻辑凭据身份，实际 `/v1/inventory` 必须始终有零个
注册 Node。它不启动 Local EAIOS、机器人、模拟器或 Provider，也不报告物理证据通过。
