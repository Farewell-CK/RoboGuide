# Chat 通用只读状态查询 v0.1

本实现位于 `feat/chat-agent-loop-20261113`。复用原 Controller 的 tenant/mTLS 与
`GET /v1/state/providers`、`GET /v1/inventory`、`GET /v1/state/records`，
不改变 Controller/Node、State schema 或设备执行权限，也不创建 Mission。

## 使用

在 Chat 中直接提问“dog2 还有多少电”“它在哪里”“姿态是什么”。模型使用
`list_state_fields(target)` 发现当前身份可见的状态对象；target 为空时列出目录。
`read_state(target, fields)` 查询明确字段；字段可用中文或目录返回的英文名。
目标必须来自原始问题、已显示引用或本轮实际读取，不能猜测 Node/设备映射。

无需模型的入口：

```text
/state-fields
/state-fields dog2
/state entity-dog2 电量 位置 姿态
/state #1 battery
/state
```

`/state #1` 只引用上次完整显示的状态目录；`/state` 重查最近显示的状态对象。
对象含空格时使用引号。状态对象 ID 和上报 Node ID 可用于查询：前者来自原始 export，
后者只表示查询该 Node 声明的对象。二者命名冲突、同一字段有多个对象或来源时返回歧义，
请先 `/state-fields` 查看并使用明确对象 ID；不选择第一个值或把多个来源合并成事实。

独立安装 CLI 同样可用：

```bash
roboguide state-fields --profile /path/to/workbench.toml
roboguide state entity-dog2 battery position --profile /path/to/workbench.toml
roboguide chat --profile /path/to/workbench.toml --ask '/state entity-dog2 电量' --output-format stream-json
```

状态响应直接展示宿主校验后的数值、单位、地图、读取时间及原始来源。
缺失、未知、已过期、不支持和来源歧义分别显示；部分或全部不可用的单次调用退出码为 2。
历史 `/raw` 不重新读取。断开设备或没有配置状态上报时，目录和数值不会自动生成。

## 可扩展格式

`state_queries.py` 拥有目录验证、原始来源加入、读取预算、显示和冲突处理。
`state_profiles.py` 的 `StateProfile` 将精确 payload schema、对象 class/type、字段列表
及纯解码器绑定。新增已维护格式只需扩充 profile，继续使用同一个 Chat 工具和 CLI；
测试包含第三种 temperature profile，无需创建新工具。目录不能携带可执行代码，
也不会下载 schema、猜测任意 JSON 字段或自动导入 Python 插件。

首批格式：

| 精确格式 | 对象 | 字段 |
| --- | --- | --- |
| `roboguide.world.robot-physical-observation/v0.1` | World / robot-physical-observation | battery、position、posture |
| `lite3.robot-state/v0.1` | Node / robot | battery、position、posture |

原始新旧格式分别校验；不改写旧报告，也不将未知 schema 当成已支持版本。
未接入格式仍可作为目录元数据展示，但不提供推断数值。
Node 的健康、资源、任务生命周期和 World 的设备物理上报保持独立。

## 来源、时间与完整性

每个字段必须匹配当前目录中的原始 object、Node/local-system source、semantic、channel、
payload schema 与有效期。重复声明、重复记录、其他成员的记录和缺失会话/sequence 均不
提供有效数据。库存中的原上报 Node 必须具有明确 Reachable 观测；该观测不是物理就绪。
记录读取按所选声明的 reported/observed 语义过滤；两种都需要时保留完整可见响应，
然后按原始声明分别匹配，不把 observed 重新标记成 reported。

读取共享一个原始单调时钟期限；正常 State 查询消耗三次 GET，目录查询一次。
它们使用现有每轮最多四次读取、三次模型调用的预算，因此一次目录发现加一次完整查询
可在同一轮完成。HTTP 失败不重放、不换源，也不会改为创建机器人任务。

外层 TTL 不能刷新底层原采样：电量与姿态保留较短 chassis 窗口，位置保留 Mapping
response age；还要扣除同一 Controller 的接收后保留时间和本地读取、模型等待时间。
宿主在最终组织设备状态回复时再次检查原始数据，无额外 GET，也不更新原接收时间。
本轮原始读取证据与 consumption_check 分别保存，不能用模型的解释延长当前值。
同轮查询设备状态和任务进度时，保留两者的宿主结果，不因状态卡片覆盖任务回答。

位置返回所属地图、米和弧度；不转换成未提供的地点名称。Mapping 接口响应的新鲜度
不证明原始位姿测量时间、定位质量、地图 revision 或共享坐标系，输出继续声明这些缺口。
报告姿态和电量不授予移动、停止、自动重试、审批或计划执行权限。

设备选择复用原 DisplayTurn 的完整写入/flush/持久化 ACK。隐藏结果不能改变状态序号，
排队输入保留原引用，其结果只发布本轮真正展示的对象域。恢复后只保留历史引用并重新读取。

## 交互维护

帮助按设备状态、任务规划、会话诊断组织，移除实现版本菜单。首页给出状态查询示例，
输入框补全包含 `/state` 和 `/state-fields`；忙时依赖设备序号或省略目标的查询不排队。
状态栏显示最近状态对象，Ctrl-C 明确表示停止显示、原请求继续收尾。
快捷确认文案来自实际提议类型；只在计划/执行流程提议时展示 F2。
空跟踪列表、未找到跟踪对象和停止本地跟踪均给出下一步，停止跟踪不宣称取消任务。

验证使用隔离 HTTP、脚本 Provider 和独立安装包的实际 PTY。真实 Provider、部署中的
Controller/Node 及实际设备上报需要分别联调，不能用这些软件夹具替代。
