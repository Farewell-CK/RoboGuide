# 2026-11-04 手机 APK 源码与打包结果审查

结论为可构建、可交互的演示原型，尚未实现正式手机 Node 接入。
部分界面使用了真实设备地址，但不能据此认定任务、视频、地图或急停已接通。
AGY 输出中将多项演示描述为真实功能，与当前实现不符。

本轮只审查源码、APK、打包网页和 Controller 只读状态。没有修改 APK 工程，
没有安装到手机、发送机器人任务、改变 Robonix 或处理已有资源占用。

## 检查对象与实际验证

- 源码位于 `C:\Users\33457\Desktop\桌面文件夹\workspace\DEAIOS\roboguide-mobile-hub`。
- 本地 main 与远程 main 均为 `a868de3b5ab8386286e40fb1488514398db4c604`，提交时间
  2026-10-29 14:11:28 +08:00。按实际提交审查，不将名称“2.0”当作协议版本。
- APK 位于 `C:\Users\33457\Desktop\DEAIOS_Mobile_Hub.apk`，3,849,120 字节。
  SHA256 为 `84a106dd445be91495bed273eaf68477a6a4dd9bc57287181d9bce64c8a77213`。
- aapt 确认 applicationId 为 `com.syswonder.roboguide.hub`，versionCode 1、
  versionName 1.0、target SDK 34，为 debuggable 构建。
- `vue-tsc --noEmit` 与 Vite 生产构建通过。构建到独立审查目录，原 dist 没有改写。
  新构建的 HTML、JS、CSS 与 APK 中对应资源逐字节一致。
- 从 APK 直接提取网页资源，在本地浏览器打开并操作设备、FPV、任务与急停界面。
  这验证了打包网页行为，没有验证 Android 真机、音量键、后台生命周期或摄像头。
- adb 当前未发现连接的 Android 设备，没有声称完成原生安装与真机验收。
- ThinkPad 的 Controller 实时 `/v1/inventory` 只有 `lite3-dog2`，没有手机 Node。
  当时狗2 liveness 为 Unreachable，历史 reported_health 仍为 Online。
  同期 APK 网页在实机模式显示固定的“集群 4/5”、狗2 ONLINE 与 94% 电量。

原始摘要、重建结果和截图保存在工作区 `lab/mobile-apk-audit-20261104/`。
源码仓库与 APK 均保持原样。

## 六项功能逐项对账

| AGY 描述 | 代码与运行观察 | 当前判定 |
| --- | --- | --- |
| 环境与集群配置 | 配置弹窗和模式切换存在；唯一业务 fetch 仍固定读取狗2 `:8092/api/state`，未使用配置中的 Controller 地址 | 界面已实现，真实路由未接通 |
| 空间地图与指点导航 | Canvas 绘制固定仓库矩形和本地坐标；点击只更新 activeGoal 和 currentMode，没有提交导航请求 | 交互演示 |
| 设备资源池、摇杆及姿态 | REAL_DEVICES / SIM_DEVICES 是静态清单；摇杆直接修改本地速度和位姿，姿态按钮只改 currentMode | 静态设备和本地控制反馈 |
| FPV 视频与 HUD | canvas 绘制网格和模拟托盘；1080p、30 FPS、42ms、WebRTC 标签固定，没有视频连接实现 | 合成画面演示 |
| 自然语言、语音、澄清与 DAG | 松开录音按钮填入固定句子；任意目标生成固定任务与澄清选项；定时器自动增长进度，没有提交 Mission Request | 交互演示 |
| 音量键急停与保活 | 原生按键转发 JS 事件和屏幕常亮存在；没有发送停止命令、独立停止证据或 Node 心跳 | 原生交互入口存在，停止和 Node 保活未实现 |

设备地址、视频 URL、能力字符串和 phoneNodeId 出现在常量中，不构成发现、注册、
鉴权、执行或流媒体连接证据。所谓 Isaac Sim 模式也是前端轨迹计算，本轮未发现与
实际 Isaac Sim 进程通信的代码。

## 需要优先修复的问题

### P0：停止界面声明了没有发生的硬件结果

[`triggerEmergencyStop`](https://github.com/Farewell-CK/RoboGuide-Android/blob/a868de3b5ab8386286e40fb1488514398db4c604/src/services/telemetryService.ts#L462-L481)
只将本地设备置为 ESTOP、速度置零。EStopModal 却显示“集群硬件急停已触发”、
“电机扭矩已切断”及所有移动设备 BRAKED。按下按钮不产生网络命令或停止回执。
解除也只是把本地设备改回 ONLINE/stand。AGY 描述的零速度与高 PD 路径均未实现。

原生 MainActivity 在 onKeyDown 收到音量减键时即转发事件，没有长按判定。
不依赖 Android 按键试验即可确认这条代码路径最终只触发上述本地状态变更。
网页运行也复现了未收停止证据即出现硬件停止成功提示。

应先明确标识 DEMO，并在真实模式禁用未接通的动作入口；真实停止接入后分别展示
请求、接纳、任务终止和设备停止结果。动作及停止策略由 Robonix 执行，
APK 不自行承诺高 PD 或动力切断，不能用提示语代替设备证明。

### P0：手机节点只存在于前端静态清单

[`phoneNodeId` 和手机卡片](https://github.com/Farewell-CK/RoboGuide-Android/blob/a868de3b5ab8386286e40fb1488514398db4c604/src/services/telemetryService.ts#L191-L210)
为固定值 `node-phone-user`，应用没有正式 NodeSession、Hello/Register、租约心跳、
Execute/Cancel、执行 journal 或产物回传实现。原生业务代码仅为 MainActivity，
没有相关 Node 模块；Capacitor 原生插件清单为空。

手机相机采集、空间定位和实际语音识别也没有对应实现。
首版必须有真实 Node 身份、注册和任务闭环，见[设计草案](mobile-field-client-design-draft.md)。

### P1：真实模式仍能展示虚构在线状态和任务完成

controllerOnline 初始为 true，狗2、电量、温度和多个其他设备状态取自常量。
轮询失败被空 catch 忽略，没有转为不可达或显示数据过期。狗2 WebUI 查询成功也会
把 Controller 标成在线，混淆了两个服务的健康。

MissionCommander 为任意输入创建同一套前端任务。浏览器实际输入“只读取状态，
不执行动作”，仍得到“2号狗领航前出导航”和“AGV 伴随搬运”的固定 DAG。
点击固定澄清选项后，telemetryService 的计时器自行将进度加到完成。
这些操作没有向 Controller 提交任务，不能称为调度或本机执行结果。

应把示例数据与真实连接分开。实机状态来自 Controller 的 inventory、任务及 State
视图，明确来源、更新时间和过期情况。示例能力清单不能当作已注册能力。

### P1：配置与轮询存在具体错误

- [`switchEnvironment`](https://github.com/Farewell-CK/RoboGuide-Android/blob/a868de3b5ab8386286e40fb1488514398db4c604/src/services/telemetryService.ts#L348-L410)
  保存 custom endpoint，但启动仅恢复 profile ID，没有读回该 endpoint。
- EnvSettingsModal 的 customStreamUrl 输入未传给保存函数，也没有更新生效的流地址。
- 切换环境时表单仍带着原环境的地址，applyAndSave 总是传这个值，可覆盖目标 profile
  的默认 Controller 地址。实际轮询又不使用该值，因此修改地址不能证明切换了链路。
- 唯一 fetch 固定到狗2 WebUI；custom 模式不执行这项读取，也未实现另一条读取链路。
- `Number(data.pose.x) || oldX` 等写法会丢弃合法的零坐标和零航向。
- 每 200 ms 启动异步 loop，单次超时 1800 ms，未防止请求重叠和旧环境响应写回。
- 实机 preset 地址与当前 ThinkPad 部署不同；未验证该 preset 指向的远端服务身份。

### P1：人工处理入口没有对接当前 RG 恢复需求

当前澄清气泡不是 Controller 人工待办。没有原 execution、generation、证据版本、
操作者、原任务回执与正式对账解除流程。现有狗2 SafetyStop 占用仍保持 Bound，
本次读取没有改变它。人工观察提交和资源处理仍需我们在 RG 后端建立合同。

### P2：常亮不能证明 Node 在后台保持租约

FLAG_KEEP_SCREEN_ON 已实现，但应用没有心跳实现，也没有声明 Node 后台运行组件。
Android 官方说明该 flag 属于 Activity，应用进入后台时屏幕仍可正常关闭。
因此常亮、后台执行和协议租约必须分别实现及验证。
依据见 [Android 官方文档](https://developer.android.com/develop/background-work/background-tasks/awake/screen-on)。

## 建议下一轮拆分与 AGY 交付要求

沿用现有 Vue / Capacitor 页面，补齐 Android 本地执行模块与协议接线。
原型画布、页面和交互可以保留，不再把更多演示按钮作为接入进度。
同一 APK 的 Node 模块统一承载已实现的本地能力，不按每条能力另起一个服务。

| 顺序 | 我们负责的 RG 工作 | AGY 负责的 APK 工作 | 可验收结果 |
| --- | --- | --- | --- |
| A：标清真实与演示 | 提供实际 inventory / 任务样例及状态含义 | 明确 DEMO，未连接显示 Unknown/不可达，取消伪造完成和硬件成功提示，修复配置 | 未接通时不会显示已执行或已停止 |
| B：真实手机 Node | 冻结对接部署、Node Protocol / Contract、mTLS 身份及最小能力样例 | Android NodeSession、持久唯一身份、注册、租约和重连，页面消费真实状态 | Controller inventory 实际出现手机，断连后按真实租约变化 |
| C：手机自身任务 | 提供正常任务准入、Commit/Bind、结果及 Artifact 合同 | 先做无硬件副作用的 ping，再做真实手机采图、权限检查与执行 journal | RG 下发手机任务，收到原 attempt 结果；图片有真实字节、摘要及任务关联 |
| D：人员复核 | 实现任务归属、证据提交和人工对账规则 | 展示原任务、现场观察表单与回执 | 能处理真实待办，旧 generation 和越权提交被拒绝 |
| E：机器狗协作 | 在 P2 原停止/资源闭环通过后提供任务与状态 | 通过 RG 请求狗2任务，展示各层真实进度及结果 | 狗2任务与手机复核组成依赖流程，不直接绕过 RG 控制底盘 |

正式 Node 协议入口为 protobuf gRPC 双向 NodeSession。
接口形状可从 [Node 合同](../../contracts/node/v0.17/roboguide-node.proto)核对，
具体采用哪一代 Contract 以实际部署的 Hello/Welcome 协商及已验证能力为准，
不能仅根据目录中的最大版本号声明支持全部功能。
Android 本地模块实现或复用该协议，Vue 通过本地桥展示和响应本机任务。

人员提交目标另走 Mission Intelligence 的 Mission Request 入口，编译后的 MissionPlan
再进入 Controller，不能把任意自然语言 JSON 直接当成 `/v1/missions` 的既有请求格式。
现有源码使用 `/v1/mission-requests` 提交自然语言请求，部署接线需另行冻结。

节点身份与操作员身份独立授权。网络断开不等于本机未执行，重复 Execute 不重复采图，
应用重启按原 attempt 查回。取消不自动补写成功，不使用新的身份绕过未决任务。
已存在的机器狗安全占用保持原样，手机接入先在隔离软件环境完成协议测试，
之后验证真实手机能力，再进入有现场授权的机器人协作试验。

## 下一轮最小验收清单

1. 当前 Controller 的 inventory 可查询到实际手机 Node，稳定身份在重启后保持。
2. 真实状态来源和数据时间可查，关闭连接后不会保留假在线，演示不会混入实机视图。
3. RG 正常分配一个手机任务，经匹配、Commit、Bind 后取得原 attempt 结果。
4. 手机采图产生可取回的真实图片，重复请求不重复执行，取消与响应丢失均有原身份记录。
5. APK 无实测依据时不显示“机器人已停止”，界面取消与实际停止证据分别显示。
6. 交付 APK、源码 commit、APK SHA256、冻结接口样例和真实验收记录。

达到这些条件才能将交付称为“手机已作为 Node 加入 RoboGuide”。
