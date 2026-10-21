# Memory 本地内容引用生命周期验收

`/v1/content/references` 补上已有 Memory v3 `content_references` 表的正式写入、只读观察与
exact-generation release。路由只在启用 reclamation 的 same-UID 私有 Unix listener 上存在。
新声明必须通过实际 provider CAS 校验；活动引用挡住 tombstone/collect。引用释放后保留原行，
旧 acquire 重放不会重新激活；下一次 owner 生命周期必须使用新的 reference ID。

运行：

```bash
uv run python tools/quality/check_memory_reference_processes.py \
  --output /tmp/rg-memory-reference-20261021-b
```

实际结果：

- 两个原 provider 进程都正常退出（exit 0）；第二次进程重启后读到同一 provider instance 和
  仍活动的原 reference generation 7。
- exact-generation release 与相同请求重放均返回 `released`；旧 acquire 重放仍返回 `released`。
- 原 provider 的 tombstone 请求实际返回 `blocked`，并包含 lineage 引用的 reference ID。
- 进程退出后只读 SQLite 检查仍为 Memory schema v3，原 reference 行 `active=0`。
- `registered_nodes=0`、`device_facts=0`、`models=0`、`simulators=0`。
- 原证据写入 `/tmp/rg-memory-reference-20261021-f/result.json`，source SHA-256 清单为同目录
  `source-identity.json`。

导入契约包含本地 file-only operation 的 opaque Mission/Group/Task 字段用于校验原 import
identity；该夹具没有注册或连接任何 Controller、Node 或设备，也没有产出设备观测。

`integrations/memory-provider/tests/test_use_leases.py` 还验证活动 lineage 引用返回具体 tombstone
阻断原因、并发 acquire/tombstone 的串行边界、错误代次不能释放、exact release 后可继续原本的
本地回收流程，以及 Unix HTTP 路由的读取/写入和 TCP 拒绝。相关 provider 测试、strict mypy、
Ruff、function-doc 与 actual-process gate 通过。

该结果证明 same-user 单 provider 软件引用生命周期和本机 CAS 删除门禁；它不证明跨主机来源、
远端副本/Archive 一致性、Controller/Memory catalog 全局引用闭合或物理设备安全。F4/F5 仍开放。
