# ADR-0112：原 provider 内容回收与 Active Map 当前权威

状态：Accepted，2026-10-20。承接完整整改 F4/F5 的内容生命周期，不改变原计划的完成标准。

## 问题与权威

原 Active Map GC 接受调用方提交的八个 `true`，没有从原 backend 核对 import、使用许可和
真实 worker。这些声明不能授权删除。原 selection/query/handoff 也必须在实际效果处再次
检查完整原 lease、map、Node、Group 和 attempt，防止验证期间撤销或修改目标。

Local Memory Provider 只回收自身管理的副本，不宣称删除 Controller CAS、其他 provider、
snapshot 或 archive。Control 继续拥有 Group、成员与权限；Local EAIOS 保留内容和最终安全
权威。新接口仅允许显式配置的、同用户私有 Unix transport，不能通过原 loopback facade 启用。

## 决定

原布尔声明 GC 保留为拒绝删除的兼容查询。新回收命令冻结 command ID、原 provider/instance、
digest、size 及完整原 import 集合；相同 ID 不能更换内容。真实 import 记录、原使用许可的
不可逆撤销、原 backend revoke receipt、当前 Active Map、未完成 handoff 和真实 worker
共同决定可回收性。过期或重启失效不能代替已确认撤销；未知旧 import 缺少原上下文时拒绝。
配置的原生产 export 和外部保留引用同样阻止删除。调用方不能用一个空引用列表授权回收。

原 import 的冻结 preparation/session/attempt 身份直接定位原 lease，不能从另一份 grant
借用相同 import。尚未首次 grant 的原 revoke 仍可永久 fence 未来使用；它不补造一次 grant。
当前/unconfirmed lease 只做一次有界流式引用扫描，超出完整覆盖预算保持 blocked。

持久 tombstone 先于任何 unlink，并立即阻止该本地 digest 的新 import、grant、selection
及读取。历史 import、manifest、command 和审计记录保留；Imported 历史不证明当前可用。
物理文件回收在独立调用中进行，核对原命令和当前全部引用，完整重算 hash/size，拒绝
symlink/替换文件。unlink 后 fsync 父目录，最后记录原 receipt；崩溃后的只读查询不补副作用，
显式重试只对同一个已持久化的删除意图核对或完成。新 import ID 不能复活已 tombstone 内容。

同 backend 的 import、grant、job admission、active-map 与回收共用实际 store mutex。
不在这个 mutex 内等待 worker 退出。Active Map handoff 冻结完整原 target，Commit 重查
原 source head、完整 target、代际与当前使用许可；不能替换另一个 map 或覆盖已有 head。
独立 reference snapshot 必须来自实际管理组件；全局 Controller/snapshot/archive 图未闭合时
不得据本地回收声称 F4/F5 完成。

文件 backend schema v2 fences v1 writer；迁移只增加删除和历史 receipt 表，不为旧 import
补造授权或撤销证明。Node Protocol、Controller wrapper 与原 grant 预算保持原意义。
正向验收使用真实文件、SQLite、Unix HTTP、后台 CPU worker、撤销和进程崩溃；设备数为零。
