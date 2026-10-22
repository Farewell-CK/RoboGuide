# 原未发布 Archive 清理与升级验收

本包在唯一分支 `codex/nonrelease-remediation-closure`，接续 `f121c2d`，实现
[ADR-0154](../../decisions/0154-original-unpublished-archive-abort.md)。此前
Archive 的[原验收](2026-10-22-original-content-archive.md)保留其冻结版本和范围。
本包不关闭完整 N1 或 N2–N7，也不宣称设备停止或 SoftwareComplete。

## 原操作和实际 writer

显式 `POST /v1/maintenance/archive/abort` 只允许当前 global recovery-admin，
绑定精确原 copy ID、完整 intent digest、原 tenant/principal、source owner、
Controller authority 和 destination。原 cleanup intent 先于任何效果进入 SQL；
其独立 kernel budget 不续 copy 的预算。过期 cleanup 保持 Unknown，另一个显式
cleanup 可在自己的原预算内处理同一未发布 copy，但不能重发 copy。

实际 source writer descriptor 与 Archive root 的排他 descriptor 全程持有；核对
实际文件 inode/device、原 owner、Linux boot/time namespace、当前 PID/start ticks。
实际 durable tombstone 先于精确 SQL receipt。SQL 故障后的相同命令只检查并同步
原 tombstone，不重新复制、不更换证据或 budget。源 pin 只有完整 receipt 才退休。
原 copy 历史保持 pending，独立 abort receipt 不能伪造 compact。已发布 copy 拒绝
abort；partial 文件和接收方 Archive 引用保留，被 abort 的原 copy ID 永不重发。

shared wrapper v50 / Archive operations v0.2 要求 mandatory abort journal，包含
空 journal。v49 迁移验证所有原命令，保留原 intent 内 v49 checkpoint 的原字节和
budget。此次实际升级发现近期 wrapper 曾被 isolation validator 当作旧 Memory
合同拒绝；修复明确列出 v45–v49 的原 v45 isolation 语义，独立 SQL/quota/内容
authority 仍分别严格检查，未使用绕过全部验证的提前返回。

显式 recovery v0.13 延续 v0.12 的 writer、完整 consumer inventory、Catalog、
同 owner CAS 和 bundle v0.4 目录合同，增加原 cleanup journal/tombstone 对账。
有 tombstone、无 SQL receipt 仍保留 source pin；有 receipt、缺 tombstone 拒绝。
恢复到新目录本身不启动服务，不把复制品当作新的使用或物理许可。

## 实际验证

证据根目录为 `/data/workspace/roboguide-evidence/nonrelease-closure/p1/`。
每项 result 保存自身原源码、配置和 binary identity，不把不同构建合并成一份证据。

| 原实际记录 | 范围 |
| --- | --- |
| `abort-upgrade-final-02` | 实际 v49 binary 先产生原未 compact copy，实际 stop/wait 后由当前 v50 迁移、只读对账；0 Node/设备/模型/模拟器 |
| `abort-retirement-final` | 提交前单一退休确认检查的当前 binary 重跑相同实际升级/清理/冷恢复；旧 Unknown cleanup 不能补写第二份退休回执 |
| 同项的 `original_archive_abort` | 实际 create-new 文件故障留下原 copy intent；1 ms cleanup 过期保持 Unknown；新 cleanup 获得真实两个 writer、写 tombstone 后注入 SQL receipt 故障；实际退出/重启后核对同一 tombstone，无重复 copy；新 copy ID 才正常 compact |
| `abort-file-final-02` | 两个实际软件 Node、真实 CPU 文件/两次独立 verifier、原 import/use/revoke、原 Catalog 发布/退休及 SQL 回滚；原 Archive 完整 Catalog 重放和实际 Controller 重启 |
| `abort-arc-cold-final` | 从上行已退出的 SQL/CAS/Archive 进行实际 backup/verify/restore/幂等 retry，独立读原 Catalog、budget 与两份接收 CAS |
| `abort-mi-final` | 原 Request、SQL claims/nonce、中断 provider 协议 fixture、独立 Review、原目标和两个 Task 各自 import/use，保留原 child lineage；外部模型调用为零 |
| `abort-mi-cold-final-02` | 上行实际 child lineage 的 CLI backup/verify/restore/retry、旧 binary 拒绝、原 owner/CAS 独立读取与缺原内容拒绝；不启动新 Node |

升级门禁的原 producer 是实际保留的 `cd1faaf` Controller，SHA-256 为
`099e3f8a559be67b8c2cebdc4296e404920c4af837e7a38dc7a7c7814a363f89`，
原 embedded checkpoint 为 v49。`abort-retirement-final` 的 Controller SHA-256 为
`1c36eb8b167e5439593cb8152cfbc9a5299e725e8b2c0443bca367ff3eeef49a`；
文件/MI/`abort-upgrade-final-02` 的先前构建为
`4bb764ddd6c0664332b10bc61971ddb84ff1168a153d4a2eddb8646dec1f242a`。
Node 为 `7103a3ba52c4ae9829b5f67e214d936cb4826e0a5e515d78249570a33c168c1e`。
独立读取实际锁文件和当时活跃 Controller 的 `/proc` 验证回执，随后才 reap 该进程。
旧 binary 拒绝 v50；从原 Git ref 提取的旧 v0.12 CLI 拒绝新 operations marker。
删去 bundle 原 tombstone 并重新计算完整 manifest 仍拒绝，不用 manifest hash 冒充
原 authority。已发布 copy abort、不同原参数、缺已知 consumer/接收 bytes 也拒绝。

中途失败原样保留：`abort-native-01` 与 `abort-file-final` 的客户端观察预算分别
为 5 秒和 3 秒，不能据此认定已发送的维护操作无效果；原 SQL/文件保留 Unknown。
维护检查器现在单独允许 45 秒 HTTP 观察，产品 copy/cleanup/Memory/State 预算均
未放宽。`abort-upgrade-01` 暴露上述实际旧 wrapper 校验问题；
`abort-upgrade-commit` 在 20 秒启动观察内未完成初始 owner 格式就绪，保留其原
partial 文件及日志，未就地补造 owner。后续使用新目录、串行实际门禁，启动前观察
上限为 60 秒；此观察不为已接纳命令续期，也不证明生产 SLO。
`abort-mi-cold-final` 实际 backup/restore 成功后，检查器仍期待 v0.10 receipt，
因而整体失败；核对实际 v0.13 receipt 后，在新目录以明确 v0.13 断言重跑通过。
提交前 review 以实际 SQL receipt 故障复现“旧 Unknown cleanup 在另一个 cleanup
确认后再写第二份 receipt”的反例，日志为 `abort-earlier-counterexample.log`。
修复在 effect/query 前及 Commit 前核对单一已确认退休；原 Unknown 记录保留，
胜出原命令仍可精确重试，重启 journal 校验保持有效。该 contract fixture 的租约
值明确是合成输入；真实 descriptor/PID 证据来自上表实际 native gate。

## 工程检查与剩余工作

工作区 Rust **975 passed、2 ignored**；Python 全量 **3066 passed、0 skipped、
489.53 秒**，包含真实 Python 3.9 零模型/零模拟器兼容门禁。新增 recovery 错误
矩阵覆盖 Unknown/receipt、原输入/预算、实际 fence 字段与已发布冲突。Clippy、
私有 Rustdoc、517 文件格式/Ruff、327 源文件 strict mypy、函数文档及 MkDocs strict/
605 页 HTML 链接检查均通过；恢复工具单独 **90 passed**，网站 **8 tests OK**。
原日志为该证据根的 `abort-*`；不把测试数折算为完成比例。

下一必需项是接收归档显式退休、全已知生产者对账和 tombstone-before-unlink GC，
以及未归属旧 Catalog 的显式迁移、Raft Snapshot/在线 Replica 和完整 N2–N7。
source writer fence 证明受支持同主机同步 copier 的排他性，不证明 child exit、
Node/Local EAIOS/设备停止、跨主机多数派或物理连续性。
