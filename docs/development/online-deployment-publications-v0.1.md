# 在线部署信息发布 v0.1

按 [ADR-0242](../decisions/0242-online-deployment-publications.md)，两类更新均复用
现有 Controller 的认证管理员入口和单写入事务，未新增服务进程。

## 物理实体

`GET /v1/physical-entities` 返回当前完整 registry。POST 请求：

```json
{
  "command_id": "add-phone-alpha",
  "expected_revision": 1,
  "registry": {
    "schema": "roboguide.physical-entity-registry/v0.1",
    "registry_id": "my-cluster",
    "revision": 2,
    "routing_profile": "one-routable-entity-per-node",
    "entities": [
      {"entity_id": "entity-dog2", "node_id": "lite3-dog2"},
      {"entity_id": "entity-phone-alpha", "node_id": "phone-alpha"}
    ]
  }
}
```

请求须包含完整表；首次安装 expected_revision=0、revision=1。禁止隐式更换 registry_id，
禁止把已有 Actor 绑定移到另一 Node。声明后仍须实际 Node 准入和能力就绪，再经正常
任务的匹配、Commit、Bind。并不由实体声明创造能力或设备信任。

在线成功后，由部署管理保存 GET 返回的最新 registry，供下一次进程启动显式传入原物理
文件位置。接口不覆盖部署文件。checkpoint 只保存防回退摘要，不恢复“当前”路由表；
重启传入旧文件或缺文件会拒绝。

## 可行性

`GET /v1/deployment-feasibility` 返回当前版本、原文和摘要。POST 请求：

```json
{
  "command_id": "add-phone-feasibility",
  "registry_id": "my-feasibility-publications",
  "expected_revision": 0,
  "revision": 1,
  "expected_physical_revision": 2,
  "document": {"...": "完整且符合原 deployment-intent-feasibility 合同的来源文档"}
}
```

这里的 `document` 必须是原 schema 支持的完整来源，不是任意节点能力清单；示例中的
占位对象不是可执行请求。保留已有 World identity、源记录和正确内容摘要。当前实现
不扩展原来源 schema，也不会从 Node inventory 伪造地图、位置或初始世界。

新请求在写入事务内选取当前版本。原 Control 限制、原运行期 revision/candidate 保持原
来源；新发布不改写旧计划的判断。重复读取和冷恢复不重置原 preference 或证据时钟。
在线版本默认不启用首次启动才允许的 reset preferences/initial assessment。

`POST /v1/missions` 对已存在的相同 typed plan/Group，在原 writer 内核对当前调用权限、
确切原租户和原准入回执后，直接确认原接受结果，不再套用新 feasibility、重新验证
当前 verifier 来源、检查新任务容量或触发 dispatch。JSON 排版不同但解码结果相同仍是
重复请求；同 ID 换计划或 Group 仍返回 409。已发生运行期 revision 的不同计划不由
此入口复活。缺少历史 HTTP 回执的旧 Mission 不补造回执或租户。POST 保持原接受响应
（202、status=Running），当前进度仍应读取 Mission GET；重传不会让完成的任务重跑。

在线来源只在发布候选校验或冷恢复验证时编译，读路径共享不可变 Arc。编译缓存不写入
checkpoint，恢复时必须完整检查所有原文、Control 摘要及启动 lineage，全部通过才安装
缓存。health/inventory 等无关路由不解析或复制 feasibility 文档。原持久格式不变。

冷恢复继续显式提供原启动 feasibility 文件（若原来配置过）；在线发布的后续完整来源
保存在受检历史中。不要把启动文件直接替换成最新在线版，原 lineage 不同会拒绝恢复。
每类 128 版，完整在线来源总计 8 MiB；达到上限会拒绝新增，不删除历史。

两条 POST 仅允许具有全局委派的 recovery-admin，沿用 quorum/epoch/SQL 写入门禁。
正常成功为 200、冲突或格式/来源不符为 409，权限不足按原 401/403 返回。
同一主体以相同 command_id/完整输入重试返回原回执；换内容不允许重用 ID。

## 软件验收入口

```bash
python tools/quality/check_online_deployment_processes.py \
  --controller /absolute/current/integration-server \
  --node /absolute/current/roboguide-node \
  --previous-controller /absolute/prior/integration-server \
  --output /absolute/new/private/linux-evidence
```

维护脚本仅启动自有 loopback 软件进程、软件 CA、独立数据库和只读夹具，使用明确标为
synthetic 的来源。应验证旧任务保持执行时增加实体/来源、新 Node 正常绑定执行，以及
重启、旧文件拒绝、原回执保留和真正旧程序拒绝。不能作为机器狗动作或定位验收。
还须验证原 Mission 在发布前后、完成后及冷恢复后重传不新增 Attempt，不改变原准入
时间或回执；同 ID 改目标仍拒绝。大来源文档下的普通查询耗时应单独实测。
