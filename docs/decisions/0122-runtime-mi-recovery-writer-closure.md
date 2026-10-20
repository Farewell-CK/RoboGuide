# ADR-0122：运行期 MI 的恢复 writer 与原调用闭合

日期：2026-10-20。状态：Accepted，实施中。

ADR-0121 的 runtime MI SQL 保存调用前 intent、原回包、不可重发的 uncertain outcome、
原 kernel budget 与独立观察。它必须作为独立恢复组件显式声明，不能借用 Mission HTTP
的 `.service.lock` 或 Controller 的 `.writer.lock` 假定 worker 已静止。

recovery-profile v0.3 在保留 v0.1/v0.2 原语义的基础上增加 `runtime-mi` kind。恢复工具
必须同时取得实际 `database.worker.lock` 和数据库 inode lease，验证唯一实际
`roboguide.runtime-mi-work/v0.1` storage marker/instance 与 nonce schema，再开始扫描。
活动 writer、硬链接别名、旧存储或不明 marker 均拒绝；只取得一个无关锁不能授予备份权。

原 Controller、Node、Request、MI work 和 CAS 依赖在同一完整声明 profile 下冻结，SQLite
backup 保留完整 schema/rows 和原 calls/nonce/原始 bytes。新目录保留原数据，实际 lease
由正常服务 startup 按原 writer 协议重新取得；恢复操作
不改变 instance、kernel、期限、provenance 或 outcome，不启动任何服务或设备。
全量 schema/row/content 证据检查与真实旧工具拒绝必须通过；原较早数据不能取得新的模型
调用许可。更换代码、配置、证书或时间域的主动迁移仍须独立协议，不从数据可读推断连续性。

旧工具必须拒绝 v0.3，不能忽略新增 kind。静止恢复不等于在线 HA、跨主机时钟迁移、设备
连续性或总体 SoftwareComplete。其余 F0/F4–F10 要求继续按总计划执行。
