# ADR-0181：跨 owner 放弃原 Prepared 上传

- 状态：Accepted，已实施并通过本包实际进程验收
- 日期：2026-10-24
- 前置：[ADR-0177](0177-explicit-prepared-upload-recovery.md)

新 leader 的实际 Artifact writer 属于 B，不能将原 A 的 Prepared→Abandoned
事件绑定到 B。现有每条新 bare-content event 使用当前 writer 的规则使这项正常
释放被拒绝；它也不能通过伪造 A writer 或完成原 A 上传解决。

只对已有完整原 Prepared（或完全相同 Abandoned replay）允许继承其原 qualified
origin。新事件必须是 Abandoned，原完整 admission/principal/tenant/certificate/
budget/hash/size/prepared-time 必须通过原 `validates_successor`；从完整已提交的
Catalog 保护前缀与当前 tail 按原扫描上限定位，不能用传入 owner 或同 hash 猜测。
构造方和每个接收副本独立计算同一规则。其它新 bare-content origin 仍必须属于
实际当前 writer；跨 owner Prepared→Completed 仍拒绝。

沿用原 DELETE 的当前 uploader/tenant/TLS/grant 检查、原 writer/Commit 排序和
Abandoned receipt。它只释放原上传的活动输入引用，保留原历史和 origin。实际
Root Log/View/Snapshot/reader/其他 consumer 的独立保留责任不解除，不 unlink。
原 recovery 的历史 Unknown 不变；来源放弃后，pending recovery 不再满足其当前
Prepared source 检查，不得完成或重开原 copy。实际 reader/worker pin 不由 DELETE
或取消等待证明已结束。

不新增持久合同：原 v3 envelope/v32 codec/v53 wrapper 已能精确表示原 origin 和
Abandoned；旧 binary 拒绝这一跨 owner 新命令，但无需将已合法保留的历史重标记。
完整 metadata 与 bytes 的同一实际多数派、原预算和旧 writer fences 保持。

验收需要来源/目标两个真实 Artifact owner、原上传 Prepared 后的实际故障和 source
退出、新 leader 的 DELETE/重试、原 events/origins 与完整 input 集核对，以及实际
Root 字节/独立 pins 保留。合成 metadata 反例只验证构造/拒绝规则；不创建设备。

结果见[实际验收](../development/acceptance/2026-10-24-cross-owner-prepared-release.md)。
