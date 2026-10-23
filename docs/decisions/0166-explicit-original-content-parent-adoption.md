# ADR-0166：原内容主库的显式责任接管

- 状态：Accepted，实施中
- 日期：2026-10-23
- 前置：[ADR-0160](0160-original-content-producer-journal-and-interruption-reconciliation.md)

## 决策

增加显式、离线、member-local 的原 Raft content parent 接管入口。调用者必须已经按
ADR-0160 升级同一个实际 content root，并持有其原 writer。入口同时取得原 parent 的
sibling/inode writer，通过原生 DELETE journal/exclusive transaction 排空旧 SQL
连接。不存在的主库、错 profile/schema/root、旧 writer、缺 mandatory head/record 或
原内容不可验证均拒绝，不隐式创建、修复或从当前配置补造旧身份。

仅接受完整原 storage v4；已 journal 化的 v5 保留原独立合同。接管后为显式 parent storage
v6/schema v0.6。新格式强制保留有界完整接管历史，即使为空也不将缺行解释为无操作。
旧二进制拒绝 v6。内容 root 的 SQL v3/identity、应用 wrapper 和 Node 协议不变。
第一次意图与完整 first-intent 目录同事务写入原 v4 的 adoption-pending 格式标记；
普通/旧打开拒绝此标记，原意图或目录缺失也拒绝，不能退回“从未开始”的空解释。
普通 open 不将 v4/v5 自动转换成 v6，Snapshot 不传播该 member-local 操作权威。

新接管先采样真实进程/kernel 不可续预算，冻结原 parent descriptor、完整源 SQL 摘要、
当前 Log/View/Snapshot 的确切原记录、qualified inputs 和实际 root identity。原意图
持久化先于新增 producer pins、内容 hash 和接管 publication。仅有原 SQL 事务提交，
不表示接管在预算内完成；实际原 root producer 回执和同一原预算内的主库回执均须保留。
新当前保留责任使用新 operation IDs；它们证明本次接管，不表示旧 append/build/install
曾有进程、预算或完成回执。旧 raw rows、legacy pins、Unknown 和原时间一律保留。

接管只重建本主库当前责任的 journal/heads，不重写原业务记录，不复制内容、不重复
Control/Task/Execute，不退休 legacy pins。原 root 中其它 parent 或缺原 source 的
legacy 引用仍为 manual/unavailable；单库扫描不宣称全局覆盖或完整 GC 许可。

同 ID 重试仅观察原记录，不重复 hash/publication、补回执或续预算。意图后发生 SQL/
内容/进程中断，原结果保留 Unknown。未提交新格式时，新的显式 repair 可有独立原
预算与 ID，并保留旧意图及 pins；已提交 v6 后不再重做接管，原缺完成证据仍为 Unknown。
原实际 worker scope/process-end、producer publication 和主库接管结果彼此独立。

## 验收

以实际旧 storage/Root2 软件 producer 和已停止的 Controller/CAS 原输入验证：原 writer/
hardlink/旧 SQL 连接拒绝，原业务行逐项不变，真实 CAS 缺失/改动拒绝，原 SQL rollback、
deadline、kill、Unknown 只读 retry、新 repair、当前 Snapshot 与独立 reader、旧 binary/
格式 fence、缺 mandatory journal 和再次正常 append/apply/build/reconcile。保持零设备、
模型与模拟器。基础迁移并不启用生产 v0.4、网络 same-voter 内容 ACK、在线恢复/路由，
也不关闭完整 N1–N7 或 SoftwareComplete。
