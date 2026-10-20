# ADR-0113：静止恢复包与 Node journal 唯一 writer

状态：Accepted，2026-10-20，整改 F4/F5/F10。

## 权威与恢复范围

恢复必须保留完整 Controller checkpoint/command journal/catalog/upload history、Replica history、
Mission HTTP receipts、Node journal、Local Memory 元数据与实际 CAS bytes。只备份 checkpoint
或只复制 metadata 不构成内容恢复。恢复工具是部署管理 composition，不调度任务、补造
注册、续预算、清除 Unknown、确认设备停止或绕过正常启动的 schema/identity/epoch fence。

新增显式静止恢复 profile，列出实际服务组件、原存储路径与相互依赖。备份取得全部原进程
writer lease 后才读任何组件；任一组件仍由服务持有即拒绝。SQLite 使用实际 backup API，
同时保留所有表而不是筛选“重要”记录。CAS、archive 和受管理文件逐个校验、copy/fsync，
完整清单绑定各个 schema、digest、size、目录和配置 identity。未知引用或缺失组件保持
unavailable。恢复不声称跨主机或设备连续性，在线 HA 内容复制仍需其独立合同。

原 Node journal 缺少进程排他 lease，第二次 `open` 甚至会把活跃 dispatch 当成重启恢复。
新 journal 在 SQLite 打开、schema 迁移和任何 recovery 前取得 canonical sibling
`<database>.writer.lock`。最后一个应用 owner 显式 unlock；只读审计不再次打开 writer。
Node journal v13 fences v12 及以前的软件；迁移保留原 invocation、receipt、deadline、
attempt、permission 与全部历史，不增加新设备事实。Engine clone 共用原 journal。

## 不可变发布与安全恢复

恢复包在新建、私有的 staging 目录构建，完成所有 bytes、SQLite integrity 与 digest 核对后
才发布 manifest；最终目录原子 rename/fsync。相同命令重试只验证原完整包，不覆盖或混合
不同来源。失败保留可检查的 partial，未发布包不能被 restore。

Restore 要求调用方提供预期 manifest digest；逐条拒绝重复路径、目录逃逸、symlink、
未声明文件、大小/hash 不符、无效 SQLite 和越界输入。目标必须是新的私有目录，不覆盖
原存储。复制完成后再次验证，写入 durable restore receipt；调用方仍通过正式服务 startup
重获部署配置、证书、当前 State 和原预算。修改目录位置不重写任何逻辑 authority 字段。

实际验收须包含活跃 writer 拒绝、WAL 内容、完整 SQL history、真实 CAS 与 tombstone、
损坏/缺失/路径攻击拒绝、旧 writer 拒绝、崩溃残留和原服务恢复读取。配置清单及软件进程
证据不代替全栈总验收或 HardwareEvidencePending。
