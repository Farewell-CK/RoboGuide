# 静态证书部署在线采用生命周期身份

本入口解决已有 Node mTLS 静态映射无法在线进入生命周期管理的接线缺口。
不自动分配 tenant/domain，不绕过证书信任根，不改变当前机器人的物理权限。
实现所有权及恢复边界见 [ADR-0239](../decisions/0239-online-static-node-identity-adoption.md)。

## 管理命令

认证 recovery-admin 向 Controller 提交 `POST /v1/node-identities/adopt`：

```json
{
  "schema": "roboguide.node-identity-adoption/v0.1",
  "command_id": "adopt-lab-static-01",
  "registry_id": "lab-controller-identities",
  "expected_bindings": {
    "node-a": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "owners": {
    "node-a": {
      "tenant_id": "local",
      "domain_id": "lab-controller",
      "expires_at_ms": 0
    }
  }
}
```

示例摘要仅为占位说明，实际输入必须与原静态映射完整一致。owners 必须覆盖完全相同
的 Node 集合，不能增加、遗漏或替换叶证书。最多 4096 个绑定，command_id 最长 96，
其余 Node/tenant/domain/registry 字段延续原合同边界。非零 expires_at_ms 必须晚于
本次实际接受时间；零明确表示部署管理有效期，仍不能越过 TLS 叶证书的实际 notAfter。

已有 Mission 必须先有正确 tenant，且 Group/Actor 和 Commit 后尚未 Bind 的资源归属
必须兼容。缺少原归属或冲突会拒绝，不能用采用命令把任务迁给别的租户。
现有 `/v1/tenant-migrations` 负责必要的显式历史归属迁移；采用本身不迁移历史任务。

成功响应包含 `roboguide.node-identity-adoption-receipt/v0.1`、完整原命令、输入摘要、
管理员主体及原 accepted_at_ms。相同命令重试返回原回执；同名冲突或已安装其他
registry 的情况拒绝。只有数据库提交成功才发布传输策略，数据库失败不改变 live。

## 新 Node 接入与重启

采用后继续使用现有 `/v1/node-identities/commands` 的 register、轮换、撤销等命令。
新叶证书必须由当前客户端信任根接纳，并持有自己的私钥；register 不代签证书。
后续 Node 仍需完成实际 mTLS、Hello/Register、会话与租约，再上报真实能力和资源。
无需重启 Controller 或原 Node；登记凭据本身不等于 Node 已入群或可执行。

采用后的 registry 为 v0.2，必须带原 adoption receipt；原 v0.1 启动生命周期部署保持
支持。冷恢复允许继续使用原静态启动字典，但必须与原采用凭证完全一致，当前动态
记录及新注册叶来自原 checkpoint。不能修改启动字典来替代完整生命周期管理。
未知/缺失/降级的采用凭证、不同原始字典和不兼容的旧 writer 均拒绝。

## 验收

```bash
python tools/quality/check_hot_node_enrollment_processes.py \
  --controller /absolute/current/integration-server \
  --node /absolute/current/roboguide-node \
  --previous-controller /absolute/frozen/prior/integration-server \
  --output /absolute/new/private/evidence-directory
```

脚本只启动自身拥有的 loopback Controller、两个通用 Node 和只读测试源，使用软件
测试 CA 与独立数据库。先让旧 Node 持续占有任务，实际采用策略并热注册新 Node，
新 Node 完成正常 Mission 后再允许旧任务结束；检查原 PID、session epoch 和占用。
随后独立验证冷恢复与旧程序拒绝。不会连接现场、Pilot、ROS 或执行器。

本合同不补齐 PhysicalEntityRegistry 在线管理或 DeploymentFeasibility 版本切换，
两项仍在[维护台账](rg-experiment-maintenance.md)中分别跟踪。
