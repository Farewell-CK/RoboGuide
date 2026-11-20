# 导航数据保存平台

这是导航软件 v16 的通用归档服务。手机端功能集成在原来的 Android 导航软件中，导航页的“导航数据记录”入口负责本地记录、导出和上传；本服务负责接收、校验、持久保存、查询及网页回放。它不替代既有实时监控 WebSocket，也不会自动连接或部署到老师的其他平台。

服务使用 Python 3.10+ 标准库，无第三方运行依赖。SQLite 保存会话索引，磁盘保存视频、关键帧、深度、语义结果、点云、地图和事件。当前手机目标版本为 `1.4-swinl-npu-384-v16-archive`。默认包名为 `com.seaway.guideassistant.swinl`；本轮使用 `-ParchiveTest=true` 时，包名为 `com.seaway.guideassistant.swinl.archive`，应用名称 **导航助手·数据测试**。

独立测试构建与默认构建共用导航和归档源码，可与原应用及既有v15并存。手机已有版本与Mac debug签名不一致时，不卸载旧包，使用独立测试包验证。测试包数据和平台设置独立；已在目标手机安装并核对v16，完成真实D455f、千问视觉归档、完整语义地图恢复、手机ZIP导出及本机平台手动/自动保存验证；范围与限制见[实机验证报告](../docs/导航数据记录-实机验证报告.zh-CN.md)。

从工程 `source/` 目录构建独立测试包：

```bash
source ../.tools/env.sh
./gradlew :app:assembleDebug -PswinlFast=true -ParchiveTest=true --no-daemon
```

手机点击现有 **开始导航** 后、规划发起前创建记录；结束或取消导航时封存。独立的数据录制开关已移除，千问意图识别不单独建档。进入 **导航数据记录** 可导出、上传、补填实验标注或删除本机记录。

## 本机启动与手机连接

在 `source/platform/` 目录执行：

```bash
python3 archive_server.py --data-dir ./archive-data --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。默认仅监听本机。

USB 或无线 ADB 调试连接建立后，在电脑执行端口反向映射：

```bash
adb -s 手机IP:无线调试端口 reverse tcp:8765 tcp:8765
```

手机进入导航页 → **导航数据记录**，平台地址填写 `http://127.0.0.1:8765`，点击“保存平台配置”。此地址通过 ADB 转发到电脑，只适用于本机联调的 debug 应用；正式平台填写 HTTPS 地址。ADB 断开或重连后若不能上传，先检查并重新建立 `adb reverse`。

详细手机操作见 [手机端操作说明](../docs/导航数据记录-手机操作说明.zh-CN.md)。

若需对外监听，先配置访问令牌，再放在 HTTPS 反向代理之后：

```bash
read -s NAV_ARCHIVE_TOKEN
export NAV_ARCHIVE_TOKEN
python3 archive_server.py --host 0.0.0.0 --data-dir ./archive-data --port 8765
```

客户端使用 `Authorization: Bearer <令牌>`。网页输入同一个令牌，令牌不放进 URL；手机设置保存在应用私有偏好中，归档和导出不包含认证凭据。

## 当前录制范围

本版新增 `runtime_configuration`、随包 `README.zh-CN.md`、`capture-summary.json`、`data-dictionary.json`，以及可选的追加式 `experiment_annotation`。已封存/上传档案只允许追加 `artifacts/experiment_annotation/revision-*.json` 并同步增加对应计数；旧文件、导航时间和采集状态保持不可变。新增标注后平台重新核对完成状态，已接收的观测文件无需重复上传。

- 彩色视频：复用 D455 彩色流，目标 **640×480、15 FPS、H.264、1 Mbps、约30秒一个MP4片段**，不录音频。只有已封装的片段进入上传清单。
- 实际推理采集组：每次真正执行推理保存对应的 JPEG 关键帧（当前质量80）、原始 Z16 深度和对齐 Z16 深度。深度使用 gzip 无损压缩，保留宽高、stride、单位、采集帧及标定信息；同一 `inference_id` 关联语义结果。排队后被替换、未执行的输入不归档。
- Swin-L：384×288 标签图和 float32 置信度；点云每10秒选一份新结果，包含地面。
- IMU、位置与航向、VINS、路线、局部栅格、规划、距离摘要、提示/播报、用户操作、云端请求与响应、使用/拒绝原因、性能和错误事件。
- OctoMap：完整快照是确定性ZIP **`.navmap`**，含`occupancy.ot`及权威`semantics.bin`。普通`.ot`遗漏原生语义置信度/显示标记，不能单独称为完整语义树。`.ot`读回只核对占用和拓扑；`NSEM0001` sidecar逐字段读回并与原树比对，保留原始RGB与语义RGB、置信度及标记。NDT1差分覆盖整个`.navmap`包。适配器先校验APK/解压库SHA-256和实际加载库GNU build-id，不匹配就记录失败；目标手机的完整树运行验证仍待完成。局部占据叶子导出另标范围，不替代完整包。

连续原始 RGB 和按相机30 FPS连续采集的两种深度不归档。压缩视频和少量关键帧保留画面；选帧深度保留数值，但这份档案不等同于连续原始 RGB-D 的无损备份。

## 上传模式与状态

手机支持仅本地保存、手动“上传 / 继续上传”和自动上传。自动上传开启且已配置平台时，约每15秒检查一次记录，上传当前已经封存的文件；失败的记录会在后续周期重试。正在写入的 MP4 和当前事件段不会作为完整文件上传。

`recording` 会话可以逐步提交扩展清单，新增已经完成的文件；此前声明的文件路径、大小、校验值和描述不能改变。导航结束后提交最终清单，再执行整份档案完成校验。因此“当前文件已传完”和“整个导航已结束并保存完成”是两种状态。

| 状态位置 | 字段和值 | 含义 |
|---|---|---|
| 手机采集清单 | `state=recording` | 正在记录 |
| 手机采集清单 | `completed` / `incomplete` / `interrupted` | 正常封存 / 录制有缺失或错误 / 异常退出后恢复 |
| 手机上传状态 | `local` / `uploading` / `receiving` / `failed` / `complete` | 仅本地 / 上传中 / 当前片段已传且仍在记录 / 失败 / 平台最终确认 |
| 平台状态 | `state=uploading` / `complete` | 继续接收文件 / 最终清单全部校验保存 |
| 平台详情 | `archive_state` | 原样显示手机的采集状态，与平台保存状态分开 |

有缺失的档案也可以把现有文件全部保存到平台；平台确认 `complete` 不表示手机采集本身没有缺失。

## 通用协议 v1

所有路径从 `/api/v1` 开始，JSON 为 UTF-8；需要请求体的请求提供 `Content-Length`。会话 ID 最长100字符，只允许字母、数字、下划线、连字符，首位为字母或数字。文件用会话内相对路径标识，不能包含 `..`、空段、反斜线、绝对路径或符号链接。

### 1. 创建或扩展会话清单

`PUT /sessions/{session_id}`：

```json
{
  "schema_version": 1,
  "session_id": "nav-example",
  "state": "recording",
  "started_at_ms": 1793000000000,
  "metadata": {"model_width": 384, "model_height": 288},
  "events_count": 2,
  "dropped_records": 0,
  "write_errors": 0,
  "files": [
    {
      "path": "events-00000.jsonl",
      "size": 123,
      "sha256": "<64位小写SHA-256>",
      "type": "events"
    }
  ]
}
```

清单自身不放进 `files`。每个已封存文件有大小与整个文件的 SHA-256，可带类型与简要元数据。清单上限 **16 MiB**、20,000个文件、单文件32 GiB。

相同清单重复提交是幂等的。旧清单为 `recording` 且平台未完成时，新清单可以增加文件并更新会话统计；必须完整保留所有旧文件描述，修改或删除旧条目返回409。结束后用同一接口提交 `completed`、`incomplete` 或 `interrupted` 的最终清单；最终清单不能再修改。当前实现使用此扩展清单机制，没有独立的 `descriptor` 或 `content` 路径。

### 2. 查询服务端保存进度

`GET /sessions/{session_id}/upload-status`：

```json
{
  "session_id": "nav-example",
  "state": "uploading",
  "archive_state": "recording",
  "completed_at_ms": null,
  "files": [
    {
      "path": "events-00000.jsonl",
      "size": 123,
      "sha256": "...",
      "type": "events",
      "received_bytes": 64,
      "complete": false
    }
  ]
}
```

偏移以平台持久文件实际长度为准，服务重启后可查询。客户端重连先查询，不能因请求超时直接假定失败或推进偏移。

### 3. 上传文件分块

`PUT /sessions/{session_id}/files/{相对路径}?offset={字节偏移}&sha256={完整文件哈希}`，请求体为二进制，单块最多 **4 MiB**：

```json
{"path":"events-00000.jsonl","received_bytes":123,"complete":true}
```

每个文件从 `offset=0` 开始顺序写入。相同内容的重试直接确认，不重复追加；偏移跳空、冲突内容或部分覆盖返回409。最后一块写入后计算整个文件哈希，通过后原子转为正式文件。校验不通过返回422，清除损坏的临时文件，从0重新上传该文件。

### 4. 最终确认

`POST /sessions/{session_id}/complete`，请求体 `{}`。

会话仍为 `recording` 时返回409。最终清单中所有文件存在、大小与 SHA-256均匹配时，更新 SQLite 并返回 `state=complete`；缺失或损坏返回409及 `missing`、`corrupt` 清单。重复确认成功是幂等的。

### 查询、事件和下载

| 请求 | 返回 |
|---|---|
| `GET /sessions` | 最近1000份记录的状态、大小、时间和元数据 |
| `GET /sessions/{id}` | 清单及接收进度 |
| `GET /sessions/{id}/manifest` | 当前/最终清单JSON |
| `GET /sessions/{id}/events` | 所有已完整接收JSONL段中的最近200条可解析事件 |
| `GET /sessions/{id}/events?offset=0&limit=500` | 按文件顺序分页，`limit` 为1～2000 |
| `GET /sessions/{id}/events?since_ms=...&until_ms=...&limit=...` | 按 Android单调时间毫秒过滤；不是Unix时间 |
| `GET /sessions/{id}/files/{相对路径}` | 校验后下载文件 |

文件下载支持单个字节区间，例如 `Range: bytes=100-999`，返回206、`Accept-Ranges: bytes` 和 `Content-Range`，用于视频拖动播放；不支持多区间请求。下载前重新验证文件大小及哈希。

## 平台页面

页面提供导航记录、采集/保存状态、文件清单、清单下载和最近事件。已完成文件可下载；MP4可播放并按播放时间查询过程事件，JPEG可直接查看。语义标签、置信度、局部栅格和gzip深度有二维预览；深度指针显示原始值及换算距离。点云提供相机坐标俯视抽样显示，下载文件保留其全部已记录点。

网页的二维预览不代表完整三维OctoMap交互浏览。浏览器若不支持gzip解压预览，仍可下载文件交给离线工具处理。

## 完整地图包与离线解析

`.navmap`使用ZIP STORED固定条目，不依赖普通`.ot`保留自定义语义。包内恰有`occupancy.ot`和`semantics.bin`，后者为小端`NSEM0001`：96字节头加每个树节点25字节，包含节点路径/层级/子掩码、占用原始位值、原始RGB、语义RGB、置信度位值和语义显示标记。置信度按原字段保存，不假定范围一定为0～1。

NDT1仍是gzip容器的无损字节差分，base_sha256和目标sha256对应完整`.navmap`。首个及周期完整检查点、顺序差分与结束包按generation隔离。导航地图仍有距离裁剪，历史需要沿途的版本链，不能只靠结束包恢复所有过去位置。

从工程`source/`目录使用解析器：

```bash
python3 tools/inspect_navigation_archive.py exported.zip --json
python3 tools/inspect_navigation_archive.py exported.zip --reconstruct-maps ./restored-maps
python3 tools/inspect_navigation_archive.py exported.zip --map-info "artifacts/octomap_full_checkpoint/<编号>.navmap" --json
python3 tools/inspect_navigation_archive.py --map-info "./restored-maps/<会话>/generation-<编号>/<文件>.navmap" --json
```

替换尖括号为实际清单/恢复路径；恢复目录必须为新建或空目录。`--map-info`既可检查档案内完整检查点，也可在不提供档案参数时检查一个独立恢复包；差分文件应先恢复，不能直接传给`--map-info`。工具对每个恢复文件校验哈希，并检查sidecar长度、路径拓扑、计数等格式约束。旧`.ot`字节链可兼容解析，但不承诺其完整语义。

原生适配器当前只支持SHA-256 `cf6006b3dd366788a88febe994302be77d431c62d501feabeaa58a349261ed61`及build-id `7bf75c29a7ba8e49050444dbb36867b0bc487a0d`对应的库。更换原生库后必须重新核对布局和验证，不能去掉指纹保护继续使用。

## 持久保存与备份

```text
archive-data/
  archive.sqlite3
  sessions/<session_id>/
    manifest.json
    partial/<文件路径>       # 已接收的临时字节，重启后继续
    files/<文件路径>         # 大小和SHA-256已通过
```

平台使用 `fsync`、原子重命名和 SQLite `synchronous=FULL`，完成确认后再次查询/下载仍应一致。磁盘故障恢复由部署环境的备份保证；备份时同时保存数据库与 `sessions`。服务不会自动删除或覆盖已归档数据，手机上传成功后也默认保留本地档案。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖本机HTTP上传、活跃会话新增文件、最终封存、重复块、断点和重启、缺失/损坏校验、视频Range、事件分页与时间过滤、认证和路径边界。自动测试结果与目标手机实测结果应分别记录，不能将协议或NSEM0001解析测试通过视为硬件视频编码、原生内存字段导出或D455导航性能已验收。
