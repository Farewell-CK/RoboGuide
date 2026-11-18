# 2026-11-09 两台 Thor 主机接入同一 RG Controller

## 验收结论与边界

VERIFIED：两台真实主机使用独立 Node ID、客户端叶证书、状态目录及资源标识，
同时接入 ThinkPad 的同一个 Controller。两个主机 State 来源不同、未过期，报告的
Linux boot ID 不同。一个正式 Mission 的两个 Role 分别在两台主机完成只读检查，
任务完成后 allocations=[]、unknown_attempts=0。狗1 Node 单独重启后重新接入，
原 Mission 及两个 attempt 仍为 Completed，未新增执行。

Operator-reported：本次两台底盘尚未上电。载波为 down、没有电池数据是观察事实，
不能单独用来证明底盘断电。主机 ONLINE、只读检查 READY/SUCCEEDED 均不证明
机器人执行就绪；状态显式保留 chassis_power=not_observed。

未验证：底盘上电后的姿态与电量、狗1 Pilot 委派、两狗运动、导航、共享地图、
物理停止/断链接管、MI 模型规划、HA 和动态身份生命周期。没有给任一 Pilot 下发任务。

## 当前部署

| 项目 | 狗1 | 狗2 |
| --- | --- | --- |
| Node ID | lite3-dog1 | lite3-dog2 |
| Tailscale | 100.117.131.75 | 100.111.156.77 |
| 当前局域网地址 | 192.168.2.49 | 192.168.2.22 |
| 实际硬件报告 | NVIDIA Jetson AGX Thor Developer Kit | NVIDIA Jetson AGX Thor Developer Kit |
| boot ID | 4522e661-5829-4d27-a059-5822aad0f72b | 9221244f-1f81-4374-b7d8-c5433298dc13 |
| 主机检查 | Completed | Completed |
| Node / Connector | active + enabled | active + enabled |
| 用户服务驻留 | Linger=yes | Linger=yes |

Controller 仍为 ThinkPad 192.168.2.20:50051；管理入口仍是本机 HTTPS 127.0.0.1:8080，
使用现有管理员 mTLS 身份。保留原 Controller SQLite 和全部实验历史。没有另起 Controller。
每台仍只有 RG Node 与统一 Connector 两个 RG 接入服务；Robonix 自身服务独立存在。

狗1此前目录来自复制：Node ID 虽已改成 dog1，证书、资源名字及原 journal 仍属于
狗2部署。未启动这套旧 Node。新目录为 ~/rg-lab/cluster-enrollment-20261109，
使用在狗1本地产生的新 EC 私钥，由 ThinkPad 原 CA 签发独立 clientAuth 证书。
私钥没有离开狗1；CA 私钥没有离开 ThinkPad。现有静态受信证书表仅追加狗1的证书，
狗2映射保留。当前部署没有启用动态 enrollment registry 或硬件身份认证。

狗1使用全新 Node/capture/disabled-Pilot journal，只声明 system.host.inspect@v1，
不启用 delegate-profile 和 origin profile；旧复制目录保留但不作为本次执行历史。
狗2保留原 Node 状态、任务委派和 origin 配置，仅追加主机观察能力及资源。
systemd 的 80-host-enrollment.conf 覆盖接入配置，服务开机启用；未进行整机重启验收。

狗1原 Connector 启动失败：现场 atlas_pb2_grpc.py 要求 grpcio>=1.84.0，
系统用户 Python 仅有 1.83.1。为该 Connector 新建独立 venv，固定 grpcio=1.84.0、
protobuf=7.36.1、typing-extensions=4.16.0，未修改 Robonix 的 Python 环境或生成代码。
静态证书仍受有效期约束，续期/轮换需要显式运维；本次未实现自动轮换。

## 实际任务流水

实际 Mission：two-host-inspect-20261109T013116Z。
一个 Context、一个 Task、两个 Role；Context 与 Task 的 coupling_mode 均为 independent。
没有角色关系、空间路线或 SequentialHandoff。每个角色请求一个
system.host-inspection-slots/v1 Compute 单位；每个节点只提供容量 1，
联合分配因此必须使用两台节点，而非助手逐台指定 SSH 执行。

```text
管理员 mTLS POST /v1/missions/assess-initial-support
    → 管理员 mTLS POST /v1/missions（结构化 MissionPlan v0.10）
    → Mission/Task admission 与 Matching
    → Control 对两个容量池进行联合分配、Commit 与 Bind
    → 两个 Node 各收到自身 attempt 的 Execute
    → Node 固定工作流 GET 本机 Connector /v1/host/status
    → 内核主机事实 + 已有只读 Robonix observer 数据
    → Node 状态轮询 GET 同一路径，得到 SUCCEEDED
    → Controller 收到两个 Completed，完成 Task/Mission 并释放资源
```

| Role | 分配 Node | Attempt 后缀 | 结果 |
| --- | --- | --- | --- |
| host-a | lite3-dog1 | 18:inspect-both-hosts-6:host-a-1 | Completed |
| host-b | lite3-dog2 | 18:inspect-both-hosts-6:host-b-1 | Completed |

完整 execution_id 均以 attempt-39:group-two-host-inspect-20261109T013116Z- 开头。
本轮 MI 未参与，任务直接使用结构化 MissionPlan；没有模型调用或自然语言规划验收。
reported State 是独立的周期上报，不因任务成功而变成机器人能力就绪证明。

## 保留与复现证据

现场前后两台遗留 Pilot journal 各自 runs=28、events=492，逐表原始行摘要均不变。
狗1的这些旧行属于复制遗留，不能当作狗1已执行过这些任务的证据。
新狗1 disabled-Pilot journal 为独立目录。未修改、抹除或重放旧任务。

公开现场证据目录：C:/workspace/DEAIOS/lab/rg-two-host-enrollment-20261109，包含
baseline、validation、独立证书公开摘要、实际提交计划、Controller 返回、两台 retention
与重连检查。远端对应 ~/rg-lab/cluster-enrollment-20261109；其中私钥和环境配置备份
只保留在原主机，不复制入仓库或本地公开证据。测试源码不携带任何现场凭据。

软件验收：59 项 integration unittest、严格包 mypy、Ruff 及函数文档检查。
host_process_gate.py 启动隔离的实际 Controller 和两个 Node，使用本机只读主机事实，
断言两个不同 Node 的 attempt Completed 及资源释放。此隔离测试没有连接机器人。
原 Node 修改的 142 项测试与 Clippy 证据独立保留于 2026-11-06 field 目录。

底盘上电后的下一步仅检查 Robonix 服务、底盘状态/电量和能力 readiness。
主机入群通过不自动授权动作，运动仍须由 RG 任务委派到 Robonix Pilot。
