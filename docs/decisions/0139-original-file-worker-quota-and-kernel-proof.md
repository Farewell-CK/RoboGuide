# ADR-0139：原文件 worker 配额与实际内核证明

状态：Accepted；补齐 ADR-0133 的实际声明、原 SQL 绑定与证据边界。

## 决定

部署分别配置 wall deadline、CPU seconds 和 address-space bytes；默认 wall/CPU 为五秒、
address space 为 256 MiB。原配额在 admission 的同一 SQL 事务持久化，spawn 只能读取
原配额，不能因重启或配置变更修改原值。原结果仍只读复用，不启动第二个进程。

`file_worker` 必须取得完整配额声明，缺少任一值都在读取输入前失败。设置 soft/hard
limit 后，从实际 kernel `getrlimit` 读取完整同值证明；父进程核对 exact original quota
和原 PID，持久化证明后才发第一份 read permit。仅配置了 setrlimit 不能描述为实际
已执行限制；health 说明 deployment configuration，原 job query 才暴露具体已验证证明。

File process SQL v2 / format v0.2 保留原预算、instance、调用与退出历史；旧 binary 拒绝。
原 v1 记录缺少 quota/proof 保持 unavailable，不补默认值。Memory provider 的复合 lease、
原 input permit、取消/wait、Unknown、GC 和安全门禁保留。恢复完整 SQL 自然保留新字段，
不建立新的 Task/Mission、Node 或设备事实。

实际 kernel limit 与 CPU/内存压力验证只证明本地软件进程边界；Control 可分配 quota、
GPU/交通成本、完整时空、硬实时与设备安全仍由相应完整阶段验收。
