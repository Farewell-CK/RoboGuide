# ADR-0119：有界 Operator 网络入口与提交查询隔离

日期：2026-10-20。状态：Accepted；实现与实际进程验收随本次整改提交。

Operator HTTP 当前包含同步授权文件、writer、SQL、只读查询和大量 inline mutation。
只把部分 SQL 放入 blocking pool，不能保证其他分支等待原锁时归还网络 worker。
为每个请求 future 的同步 poll 复用 ADR-0118 的 worker handoff；poll 返回 Pending
后不持有额外 worker/线程，socket/body 仍由原异步 reactor 驱动。不启动第二套 Runtime、
不复制权威、不改变实际 TLS witness、writer、私有 candidate、Commit 和 outbox 顺序。
current-thread/offline 调用保留同步行为，不承诺其网络并发。

入口在 TLS 前取得有界连接 permit，默认 128、显式部署范围 1–1024。满载直接关闭新
transport，尚未解析命令，没有接收或完成 ACK。permit 覆盖 handshake、request、事务和
response；TLS/request/response socket 各用原 30 秒网络预算，SQL 副作用不因 response
timeout 被取消或自动重试。显式环境进入既有 replica policy digest，旧 binary 拒绝。
连接 active/limit/rejected 是固定标签的本地观察；一秒采样，不授予业务或恢复权限。

授权文件每次仍真实读取；若完整 profile 与当前已提交 profile 完全相同，只读检查无需
排队 writer。变化仍按原 writer 重新核对并持久化后可见；mutation 一律保留实际
AuthorizedRequestGate 的排队后当前证书、委托、tenant 和路径校验。该快速路径不宣称
读请求在所有外部撤销瞬间线性化。授权更新、registry/identity/attestation、verifier、
Memory admission/query/handoff、resume/review 与 Mission admission 的私有 candidate
工作不持有 live Controller mutex；只有短 clone 和 Commit 后发布持锁。
metrics 在短锁获取已提交只读快照后查询 SQL，慢存储不占 live authority mutex。

验收要求单个实际 Tokio worker、被真实线程持有的原 mutex 和实际 TCP；同时以实际
mTLS/SQL/inotify 的排队撤销和连接容量检查确认没有授权绕过或虚构 Node。网络预算和
连接上限是软件资源边界，不构成长期 SLO、设备安全、硬实时或总体完成证明。
