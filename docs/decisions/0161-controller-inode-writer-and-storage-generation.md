# ADR-0161：Controller 实际 inode writer 与存储代际

- 状态：Accepted，实施中
- 日期：2026-10-23
- 前置：[ADR-0130](0130-replica-database-writer-and-legacy-fence.md)、[ADR-0159](0159-original-content-quorum-and-raft-reference-lifecycle.md)

## 问题与证据

`b6fe5ff` 的实际两个 Controller 使用同一 SQL inode 的不同 hardlink、不同 CAS，
能够同时启动并返回健康。canonical sibling lease 只能覆盖路径别名，不能覆盖 inode。
main 局部 File 也不能代表所有克隆 SQLite 连接的真实生命周期。已有 State probe 的
inode lease 不是产品修复，Replica 的独立 writer 不替代应用 writer。

## 决策

所有 runnable Controller 在 SQLite 访问前同时持有 canonical sibling 与实际 SQL
inode 的排他租约。实际 regular descriptor 与路径的 device/inode 必须一致；原 SQL
连接结束前不得释放任一租约。所有 EventLog clones 共享同一个实际 managed connection，
关闭最后一个连接后才显式 unlock，非 writer child 继承的描述符不延长 writer authority。
旧未持有 inode lease 的连接还须由 SQLite 原生 DELETE journal transition 与 exclusive
transaction 排空；不能从 flock 成功推断旧连接不存在。失败在 schema/evidence 修改前拒绝。
已完整接受的 v1 在双租约与持久 fence 下正常重开，不重复 DELETE transition；独立
read-only observer 不应阻止普通重启。legacy admission 与原代际迁移仍必须原生排空。

独立显式 Controller storage v1 提供持久旧 writer fence，不改变 wrapper v52、event
codec、Node 合同或原 checkpoint/预算。`ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA=1` 选择
完整格式；缺省兼容 profile 仍为 legacy，不宣称 legacy 已有持久 downgrade fence。
同一新 binary 的 legacy profile 也必须持有实际双租约并排空旧连接。未来完整内容 Replica
部署须显式选择 v1；尚未接入的新部署不能由一个 env/JSON 声称完成。

v1 在所有原 writer 与原生排空门禁下原子保留 events raw rows，改用实际私有 event table，
保留确切只读 legacy view。旧 index 名禁止重建，使旧启动路径在 schema 初始化即拒绝，
包括没有任何事件或 checkpoint 的原存储。mandatory format、原 tables/views 与 user_version
共同验证；缺失不是新库，不自动补建。没有 writable view trigger，不伪造原历史身份。
普通 library legacy open 拒绝 v1；显式 managed constructor 才可写入。

静止 recovery v0.17 显式声明 Controller storage v1 或 legacy，并持有实际 SQL inode 与
sibling。原 v0.16 及更早 profile 拒绝 v1；v0.17 完整保留原 v0.16 内容/引用/预算门禁。
restore 不启动服务、不续期；正常双租约重新绑定实际恢复文件，而不把旧 inode 当作新
设备身份。新 generation receipt 是存储格式事实，不是执行、停止或 quorum 证据。

## 验收与剩余

保留实际旧 binary/相同 inode 双进程反例，覆盖新/新 hardlink、旧 live/new migration、
原数据升级、旧 binary restart 拒绝、零事件 fence、Connection clone 与真实 child 继承、
缺 mandatory row/view、SQL 回滚和同一原内容冷恢复。旧 live writer 不被工具擅自停止。
实际 native wait 与健康/TLS/SQL 原始观察分别保留，0 Node/device/model/simulator。

本项不关闭 Replica 原内容网络多数、旧 child 日志迁移、完整在线恢复、其他 listener/I/O、
hot-journal 维护或其余 P1–P7；只读副本和 software process 不能建立物理连续性。
