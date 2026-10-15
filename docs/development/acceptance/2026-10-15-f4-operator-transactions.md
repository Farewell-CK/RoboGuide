# F4 Operator 事务写入前的当前授权

日期：2026-10-15。状态：当前竞态切片通过，整体 F4–F10 继续实施。

## 问题与实现

Operator 在 HTTP parsing 后复核身份，随后释放 writer gate；原实现把 principal 字符串传入
后续修改。请求等待真正的事务写锁时，撤权、降权、tenant 或 validity 变化可能使此前授权失效。

当前 `AuthorizedRequestGate` 保留实际 TLS leaf/notAfter、稳定 principal、委托和已选 tenant。
它取得原来的唯一 writer mutex 后，重新读取部署 profile、核对 Control 已提交 registry、
证书有效期/撤销/role/tenant 以及当前 Mission ownership，再返回这个真实 mutex 的 guard。
应用事务持有同一 guard 到 Commit/rollback；没有嵌套锁、全局线程上下文或伪造 guard。
Metadata、Memory query/import、attestation、verifier、Mission/恢复/priority/preemption 和 archive
路径均保留这个检查。内部 startup/timer/Node 输入的可信进程边界保持明确。

Certificate 时间使用原 Controller clock anchor 加本进程 monotonic elapsed，故等待锁也会消耗
原有效期；重试不延长证书寿命。对部署文件未提交的新 revision 拒绝旧请求，显式重试重新
进入正常授权同步。原主体仍被原样写入已有的 command receipts。

真实 SQL 阻塞检查暴露了同步 registry transaction 对 async worker 的影响。当前 registry
事务使用 runtime 支持的 synchronous worker handoff；current-thread/离线测试直接同步执行。
这是当前 registry 路径的可用性修复，全部 blocking writer、write-disabled/degraded 和只读快照
仍由 F10 完成，不能从本记录推断它们已完成。

## 验收

- Controller 的 214 项测试全部通过。新增检查把已解析的请求排在真实 writer mutex 后，先
  提交 revoked/role/tenant/validity 更新；四种变化都保持 checkpoint、SQL 和事件数不变。
  未改变实际 grant 的新 revision 仍允许原排队命令提交。
- Clippy、fmt 和 Rustdoc（包含私有条目，拒绝 warnings）通过。
- 工具修改通过 Ruff、strict mypy 和函数文档检查；原空间文件提交的全 Python 2470 项通过。

实际进程证据：`/tmp/roboguide-f4-operator-transaction-20261015-current-g/result.json`，passed=true。
Controller binary SHA-256：`8c029b96b39a51efffc91f28c2f7d1a9a3a6fe8d611b21d8369ffb9e2d37ef46`；
wrapper v32。零 registered Node facts，零设备。

检查用外部 SQLite `BEGIN IMMEDIATE` 暂停第一个实际 HTTPS registry transaction，并通过
Linux inotify 观察握手、原 grant read 和 transaction read。第二个真实 mTLS 连接在原 profile
被读取后等待 writer；随后原叶证书被撤权。解除 SQL 阻塞后，第一个事务返回 200，第二个
返回 409，原 registry revision 保持 1，第二个 command 未提交。随后原管理员返回 401，
轮换证书继续以稳定原 principal 查询/重试，重启保留 authority，回退 profile 被拒绝。

`*-current` 到 `*-current-e` 的早期故障注入记录保留了 harness 窗口识别和 worker 阻塞的失败；
`*-reviewed` 使用构建前的旧 binary，只是历史检查，不能作为当前竞态验收。
current-f 保留前一实现的通过记录；current-g 冻结最终 binary identity 并验证完整窗口。

```sh
uv run python tools/quality/check_identity_processes.py --skip-build \
  --output /tmp/roboguide-operator-transaction-check-new
cargo test -p integration-server
```

工具要求已构建的当前 binary、Linux inotify 和新私有输出目录。它检查软件授权与事务，
证书或 enrollment metadata 不建立物理设备身份，也不关闭其余计划工作包。
