# ADR-0106：Group Memory 运行期使用租约与本地 active-map

状态：接受设计，实施中。属于 F4，未通过完整调用链验收前不得报告已完成。

原 Memory preparation 的 120 秒是准备、导入和首次 Execute 接纳窗口，不能因运行期续约而
改变。另设使用租约：绑定完整原 preparation digest、tenant、Group、Task/Role/attempt、
当前 Node/session、原 provider/config/backend、当前成员与内容策略。初次授权和每次续约
均消费现有 Control 权威；所有参与方持久确认前不得发送整组 pending Execute。

租约命令有固定 ID、单调 generation 和最长 30 秒接收预算。重复命令不重新计时；断档、
已到期、重启后 boot 不同、当前 session/配置改变或无法证明原结果时只能停止/对账，不能
重做导入或另建 attempt。撤销先持久 fence，可先于授权到达，之后不可复活。保留当前与
最近三次命令，持久 generation 水位拒绝更旧重放；完整 Controller 审计仍归原 authority journal。

Controller 与 Node 各用自己的 receive clock，绝不比较独立主机时钟。参考 Local EAIOS
明确部署为与 Node 同一 Linux kernel/time namespace、同用户私有 Unix socket：Node 保存原 boot/uptime
deadline，后端按自己实际读取的同一 boot/uptime 验证，传输排队不能延长 deadline。
同 boot 的独立 time namespace 也被拒绝；旧预算缺少 namespace 为 unavailable，不能从当前
进程补齐。这只是 Local How 的显式合同，不把同 kernel 假设推广到跨主机 provider；其它实现
必须提供等效接收预算和撤销保证并单独验收。不存在硬实时或硬件时钟同步声明。
Linux [time_namespaces(7)](https://man7.org/linux/man-pages/man7/time_namespaces.7.html)
说明 `/proc/uptime` 也受 namespace offset 影响。参考 backend 独占本地 lease writer，启动时
持久 fence 旧使用许可，保留原命令/期限/回执；namespace 销毁后 inode 重用也不能复活旧许可。
运行中原结果必须 readonly 对账，新使用通过显式恢复，不能在后台重启后自动续约。

Node 与 backend 都在首次效果、每个有界内容读取/计算阶段及新请求时检查当前租约；到期
或撤销只允许退出/清理。已有 CPU worker 退出后才能报告软件 Cancelled，租约 ACK 不能
构造物理停止或 safe-state。Controller 保留原 F1 整组 stop/hold 和未知 ownership 门禁。

本地 active-map 属于 Local EAIOS：验证真实 imported bundle、完整 raw map、tile index/digest、
frame/anchor/units/schema 后才选择一个不可变 revision。选择绑定租约和 attempt，generation
CAS 防止并发替换；重启后的选择是历史，不自动成为当前可用地图。只有实际加载/查询完成
才能报告语义可用；文件选择、CPU 空间查询与设备 localization 是独立事实。

协议采用显式新 Node/config/catalog 版本协商。旧 preparation/历史保留，但旧 provider 不会
通过新运行期能力匹配；缺失新 authority 的 checkpoint 不从当前注册补造，危险回退拒绝。
验收必须包含实际进程、Unix/正式 Node/mTLS/CAS、当前授权、续约/撤销/到期/重启/乱序，
真实文件/空间计算和独立 verifier；不创建虚拟设备，不运行实验、模型或仿真器。

具体版本为 Node Contract v0.10、config v0.11、catalog v0.13、preparation v0.2、Node
journal v10 和 Controller wrapper v37。旧 preparation v0.1 的 schema 不增加 use 字段，
当前注册不能补造其缺失能力。Read 续约与 Import 初始授权分开；撤除 Import 不隐式撤除
已获准的 Read，撤除 Read 必须 fence 正在使用的原 context。

参考文件操作采用实际后台 worker：HTTP 先持久接纳原 invocation 和计算预算再返回 handle，
status 读取真实结果；同 ID 不再启动 worker。显式部署计算预算为 1–300 秒，默认 5 秒，
重启保留原值并把中断计算标为 Unknown。该合作式预算不建立 OS quota 或硬实时合同。

文件发现的幂等发布先核对完整已发布 manifest，再校验实际本地 bytes；相同源不重复上传。
这只避免重复发布，不声明 CAS 当前可用，消费仍需要原导入、当前 bytes 及使用许可。
导入收到 Node Persisted 后停止重发，在原期限内等待原完成；期限、绑定和权限失效仍 fence。
新协议 Cancel 先于 Execute 时只发 durable receipt，不构造从未执行的终止事实。

当前检查保留整改前实际大文件未能在 120 秒窗口内完成的失败产物；修复后的闭环仍使用原
120 秒预算，不通过延长期限把失败改成成功。生命周期检查使用真实源文件与显式有界 CPU
工作量，不冒充设备实验。
