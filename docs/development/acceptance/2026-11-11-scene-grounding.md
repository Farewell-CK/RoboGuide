# 2026-11-11 Scene 目标接入验收

本轮为已有接入授权内的无运动软件修复。两条单狗站立实验另见
[站立及路由核查](2026-11-11-two-dog-stand.md)。此处没有批准导航、共享通道或故障注入。
保留原照片接力、昨日预检、今日失败样本和各版冻结源，不覆盖旧证据。

## 现场事实与修复

预检两狗均在线，Pilot 委派入口 READY；Scene 均返回七个区域。狗1茶水间门口
`anno.2cb4c8ba`，狗2王老师办公室门口 `anno.4c5ac08a`，均使用自己的原生来源。
两边地图标签相同，但不据此建立共享坐标。原 Executor 的 Atlas 状态仍为 REGISTERED，
不是 ACTIVE；入口 READY 与所有本机执行能力就绪分别记录。本轮狗2随后有真实站立
原事件，不能仅靠一个 Atlas provider状态推断所有合同必不可用。

狗1 Connector累计重启644、狗2累计803；短窗口PID和计数不变。精确RG单元journal为空，
没有退出堆栈，未精确归因。Status构造不连接Atlas，不能把本机暂不可达直接归因为
构造RPC崩溃。手动切换后的NRestarts归零只属于systemd计数重置，不证明历史问题已修好。

新增 Scene 缓存观察器，复用原 Connector 传感器工作器，保留底盘独立采样。不改原生
服务、不启动Scene、不切图、不调用拍照或导航。各Node增加独立World导出，MI显式接纳
限定schema。新增纯转换器从RG的原World/registry/姿态检查构造准备快照，不能凭空授权。

## 实际发现的浮点传输缺陷

初版v0.1的现场链路未通过：两狗各九个数字经Node/Controller JSON链路变成相邻f64。
例如 `11.399093130268493`→`11.399093130268492`、`1.6169695854376407`→`1.616969585437641`。
原目标摘要未变而重算不一致，MI及转换器正确拒绝，未发起导航。尚不能仅据端点确定
是哪一个Rust解析或重编码步骤导致变化。

失败证据：`scene-pipeline-verification-1794363450650310220.json`。初版软件回归通过不能
替代此真实传输验收；该失败没有被删除或宣称通过。

v0.2改用唯一 `definition_json` 原canonical字符串，摘要校验UTF-8原字节后严格解析，
移除外层数值镜像。没有舍入、epsilon、重算替代摘要或借狗1目标补狗2。
原 mode 空/null、generation null仍保留，不升级定位、不可变地图、原子性或到达。
详见[ADR-0247](../../decisions/0247-lossless-scene-target-grounding.md)。

## 软件与现场发布边界

最终冻结源目录为ThinkPad `~/rg-lab/two-dog-patrol-20261111/source-r4`，原测试venv独立运行；
不是第二个现场Controller。代码不改Controller Rust核心或Node Protocol。

发布复用两狗既有Node、Connector和原Pilot journal，保留既有照片provider/consumer；
ThinkPad复用原MI数据库、Controller数据库和Artifact目录，保留全部审批规则。
新版配置及drop-in另存，旧程序目录、v0.1部署和原回执仍可审阅。没有清空历史。

验收运行结果见同目录证据索引及原 `scene-tests-r3.json`、`scene-v02-pipeline-verification-*`。
发布与最终状态以实际回读为准，不能用先前静态测试或直接HTTP读取替代中央及MI接线。

最终验收结果：

- v0.2的MI全量739项通过。r3接入测试暴露未知Scene查询路由的fallback缺陷，补充明确
  404拒绝后，最终r4接入157项通过；原r3失败日志保留。r3与r4的全部MI源文件字节一致，
  r4另复验Scene/physical grounding109项通过，未无理由重复整套MI测试。
- Ruff、严格mypy、函数文档、格式和diff检查通过。两狗旧ARM Node二进制分别validate
  新配置通过，切换后Node/Connector active；没有重启Robonix或新增常驻服务。
- `scene-v02-pipeline-verification-1794363978622262627.json`保存实际中央读取和MI capture：
  两个独立目标、零source gap；MI包含六条World evidence（各狗实体引用、物理报告、
  Scene目标），零acquisition gap。不是让操作者把坐标手填进提示词。
- 狗1茶水间来源摘要`sha256:ea3ac063819dfa39461fa9350eba141b60f875c602a1f732bc11f3e5f0c1dc49`；
  狗2王老师办公室来源摘要`sha256:e7dec2ac15aa35ee0180e383f9368bd3fce2a54151707a42c4ffbef1992d0bfa`。
  两狗schema v0.2保留各自原定义。此摘要也覆盖该狗完整原区域集合，不是单一坐标摘要。
- 准备转换器从这些原World记录生成固定目标及精确arrival predicate。未发布的到达源
  模板保持disabled。真实Controller只读计划校验返回blocked、verifier.source_unavailable。
  本轮没有提交此巡检Mission、批准导航或生成巡检attempt。

最终新增配置`node.scene-v02.toml`、MI `mission-scene-v02-service.toml`及
`99-scene-world-v02.conf`，v0.1配置和旧drop-in保留。MI readiness=true，原数据库及
审批规则保持。原到达/照片缺口没有通过修改门槛或模型手写结果绕过。

## 尚未完成

- 独立到达/停稳源的受限身份、正式登记和原plan/完整当前attempt的现场发布器；模板
  始终disabled、未发布、未登记，不是实际验证能力。
- Robonix原任务的到达后新帧证明、原JPEG/call与Artifact关联、双结果真实汇总。
- 门口冲突几何、等待点、通行权、离区证据，以及停止终局/恢复授权/Actor接替应用入口。
- 原生与RG Connector历史重启原因；两狗持续共同坐标与本机相对位置协同。

因此只把地标链路与本轮两条站立链路分别验收，不宣称双狗巡检、共享通道或接替通过。
