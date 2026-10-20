# Observability profile 与本地配置生成

`observability_profile.py` 是独立标准库工具，不依赖 MI，也不启动服务或发送通知。
它从封闭 profile 生成原生 Prometheus/Alertmanager JSON（合法 YAML）、固定规则和内容
digest。地址、故障域、实际 mTLS 文件、采样/评估/告警窗口由部署提供；不包含 Node 或设备
生成器。输出必须是新私有目录，已有目录不会覆盖。

```bash
python tools/observability/observability_profile.py \
  --profile /absolute/deployment/observability.json --output /absolute/new-monitoring-config
promtool check config /absolute/new-monitoring-config/prometheus.json
promtool check rules /absolute/new-monitoring-config/rules.json
amtool check-config /absolute/new-monitoring-config/alertmanager.json
```

Profile 字段和[合同](../../contracts/observability/profile-v0.1/schema.json)一一对应。每个服务
独立配置 certificate/server name；Controller 与 Mission 使用专用 `metrics-reader`，
不能重用 mutation 凭证代替最小权限配置。HTTP 仅允许显式数字 loopback；其他地址必须
HTTPS/mTLS。禁止 URL 内 credentials、query、fragment、未使用的 TLS 字段、symlink
credentials 或输出路径。服务不提供任意路径，按声明 kind 选择当前原生指标合同。

生产启动时由部署 supervisor 明确传递生成的文件、存储目录和 listen address。生成器
不假设已存在 Prometheus、Alertmanager、外部通知权限或 30 天 SLO；`slo_achieved=false`
只表达尚未观测该长期目标。部署运维步骤见[运行指南](../../docs/development/observability-v0.1.md)。

实际进程门禁固定使用官方 Prometheus 3.13.4、Alertmanager 0.34.1，来源为
[官方发行页](https://prometheus.io/download/)，仅作为这次可复现检查依赖，不是自动更新器：

| 文件 | 原 archive SHA-256 |
| --- | --- |
| `prometheus-3.13.4.linux-amd64.tar.gz` | `87f21a66f96c597a189cef8d640e8921b621fc17a9feff707c332c4f3b3ddd56` |
| `alertmanager-0.34.1.linux-amd64.tar.gz` | `265b9d1e55ef0d5306a436018af6d2b686c2ce051f03d968f7464ecb1372a7e8` |

解压前必须核对完整 archive；失败/中断下载不能执行。门禁另记录四个实际 binary digest
和 native configuration 检查。配置参数遵循
[Prometheus 配置](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
与 [Alertmanager 配置](https://prometheus.io/docs/alerting/latest/configuration/)；不在应用
内重新实现指标存储或告警调度。实际通知仅发到该检查拥有的私有 loopback receiver。
