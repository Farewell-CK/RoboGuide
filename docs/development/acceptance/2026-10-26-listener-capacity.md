# 真实入口容量与告警验收（2026-10-26）

[ADR-0213](../../decisions/0213-explicit-listener-capacity.md)与[实现合同](../listener-capacity-v0.1.md)
接通实际连接、线程和等待边界。创建 0 Node、设备、模型和模拟器；Spatial geometry job 为 0。
这些是单机软件进程证据，长期 SLO、跨主机和设备事实分别保留。

| 实际门禁 | 结果 |
| --- | --- |
| `/tmp/rglb-4/result.json` | Node/Artifact TLS 前容量 2，第三连接实际关闭；独立 Operator 仍回复；认证 TLS 无 HTTP2 setup 在约 15 秒后实际关闭 |
| 同一门禁 | Mission、Memory TCP/Unix、Robonix 各持有两个真实连接，超额拒绝；约两秒实际 watchdog 关闭，线程退出后 permit 回收；模型/上游请求数均为 0 |
| `/tmp/rglb-spatial-3/result.json` | 实际 Python 3.11/Open3D SDK 服务、原 profile 和 source root、私有 Unix 0600 socket，通过同样容量/关闭/回收；没有运行几何作业 |
| `/tmp/rglb-5/result.json` | 同一实际入口组合加原生 Prometheus/Alertmanager；16 条实际规则通过 Promtool/amtool；独立 metrics-only mTLS scrape；Node/Artifact 实际满载的 firing 与恢复后的 resolved 均到达 owned loopback receiver |
| `/tmp/rg-listener-identity-1` | 单网络 worker、四个实际 Operator TLS、第五连接拒绝、独立 Artifact 回复及 permit 回收；SQL 排队时原授权重查，撤销后的第二命令没有提交；注册 Node 事实数为 0 |
| `/tmp/rglb-default-1/result.json` | 不设 listener override，三个真实入口各接纳 128 个实际 socket、第 129 个关闭，独立入口继续响应。单网络 worker，顺序与 16 并发各 128 次真实 mTLS health 均为 200；p99 约为 0.012732 秒和 0.054470 秒，冻结阈值为 2 秒 |

原核心门禁 Controller SHA-256：
`377dcbd75badc79235974bcb5263c0431e6469ffcac51d745f9271f27e5b299d`。
原生监控门禁 Controller SHA-256：
`646192801ea02c7369f25ab9b632f48b367169f9f0b521dde0af2d8e063581b5`。
两次为不同实际编译文件，分别冻结 source/config/binary identity，不合并为同一运行。

监控工具从官方 release 下载并对照同一 release 的 `sha256sums.txt`：
Prometheus 3.13.4 archive 为
`87f21a66f96c597a189cef8d640e8921b621fc17a9feff707c332c4f3b3ddd56`，
Alertmanager 0.34.1 archive 为
`265b9d1e55ef0d5306a436018af6d2b686c2ce051f03d968f7464ecb1372a7e8`。
实际可执行文件各自 hash、native config、Prometheus query、Alertmanager 原生消息均保留。
原生工具实际 wait=0；Controller 使用真实 SIGINT wait=-2，其他核心服务 wait=0；
Spatial 的 SIGINT wait=-2 保留原值，不伪装正常 exit=0。

失败证据保留：旧 Artifact authorization profile 正确拒绝部署；checker 最初在 socket EOF
之后过早查询 worker、漏传 Memory mandatory input root、错误 resolve 掉 SDK venv symlink，
以及误把正常 SIGINT 当成成功 exit=0 的必要条件。修正检查器后使用新目录重跑，没有
修改原失败、旧服务实例 code fence 或 backend authority。

默认性能的原样本、attempted/successful 分母、最大值、实际机器/负载、配置与输入 identity
均保留；它测量 health 网络往返，不表示业务吞吐或长期 SLO。整包质量和固定最终矩阵
独立记录。短窗口告警送达不表示人工确认，
连接或 CPU 进程结束不表示设备已停止；没有宣称总体 SoftwareComplete。

整包当前 Rust 1200 通过、0 失败、2 忽略；主 Python 集 3285 通过、1 项缺少独立
Python 3.9 环境而跳过，另以真实 Python 3.9.25 执行标准库 bridge runtime check 通过，
provider_calls=0。恢复工具 297、Memory 88、Spatial 23、观测工具 17 项包含于主集。
严格 mypy 428 源文件、Ruff/format、函数文档、Clippy 和 private Rustdoc warnings denied
通过；双语图表同步与站点严格检查保留实际输出。额外检查不替代固定最终跨域组合。

附加的 10000 序列 allocation ownership 审计实际通过（128.03 秒）；保存反例 replay 仍是
需要真实 retained trace 的 opt-in 检查。补装独立解释器后，原跳过的 deployment runtime
pytest 项实际复测通过；没有启动 Habitat、模型或 provider。当前双语静态站检查为
863 HTML、46 条维护路由、0 个未解析 archive anchor，9 项站点单元检查通过。
