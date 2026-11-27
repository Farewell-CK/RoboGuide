# 本地导航能力与运动证据

## 能力事实的来源

机器人注册、部署导航模型、实际初态路线支持和任务满足是不同证据。

| 证据 | 说明 | 不足以说明 |
|---|---|---|
| Node capability / operation support | 部署声明的能力类别、限制与canonical操作支持 | 任意起点到任意目标都存在路线 |
| 原本地action配置及实际NavMesh设置 | 该执行实现使用的height、radius、climb、slope与分辨率 | 真实硬件已经通过能力认证 |
| reset后的有范围路线或完整组件证据 | 此初态、此场景、此profile、此目标的静态支持 | 动态执行一定成功或任务全局不可完成 |
| 实际运动请求、过滤返回与前后状态 | 本次原始执行链具体做了什么 | 单凭位置不变即证明碰撞 |
| Local skill terminal | 本地技能达到了自己的完成条件 | Mission语义或官方benchmark目标成立 |
| 官方Habitat PDDL结果 | 当前正式benchmark的终态判定 | 可以事后更改目标或排除已归档失败 |

能力数值应来自受控部署配置、机器人参数或独立能力验证，保留来源版本及实际运行指纹。
不能根据某个失败样本倒推出更大的climb/slope，也不能把未实际加载的宽松profile当成
当前能力。MI不得读取live Node Inventory；当前注册与资源承诺仍由Control处理。
跨楼层标签和“房间全连接”都不能替代机器人专用导航支持证据。

修订能力前，应在不改变目标、初态和官方判定的条件下，分别验证明确支持与不支持的
通用几何样例，确认NavMesh设置忠实表达声明，再做其他场景回归。若新增Local How，
必须版本化并披露与原执行臂的差异。已有失败结果保留原样。

## 实际运动链的只读记录

显式设置 ROBOGUIDE_B1_PHYSICAL_DIAGNOSTICS=1 后，physical diagnostics v0.6
通过实例方法tap记录：

1. step_filter调用前复制requested_start/requested_end，原方法只调用一次。
2. 原方法返回后立即复制returned_end，早于原调用者可能实施的base-offset等修改。
3. update_base调用前后读取实际base_position；不额外调用碰撞检测、运动积分或物理步进。
4. 原始异常记录类型后原样抛出，不因诊断失败替换执行结果。

base_position_after是在该action方法返回时读取，不等同于整个Gym step后物理状态。
整个step后的世界位置仍由既有position/PDDL reference采样记录，分析时应区分这两个时点。

每个agent每个post-step间隔最多8条方法记录；额外原调用继续执行并计入dropped_calls。
call_records_complete仅说明方法调用记录是否完整，字段中的unavailable仍然是缺失证据。
失败step的pending_since_last_post_step不填写虚假的simulator_step；已完成采样独立保存于
last_post_step。异常终态读取世界失败时，仍尽力保存独立的pending记录。
序列化和写入继续采用已有有界JSONL批处理；collection_stats记录采集开销与丢失。

共享世界的策略入口只转交已收到的canonical invocation，记录Mission/Task/Group/Role/
attempt/operation/destination。诊断不选择Node或实体，不授予资源或恢复权限。
新Task/attempt不会继承旧motion记录；无法获得attempt时明确unavailable，不能虚构身份。
导航profile保存实际action与active mesh的标量，允许观察原有radius buffer、分辨率
和声明差异，但不会自动修正它们。

## 如何分析停滞

| 实际观测 | 可直接确认 | 仍需独立证据 |
|---|---|---|
| requested_start等于requested_end | 本次过滤入口没有位置变化请求 | 模型/控制器为何选择该请求；是否有旋转动作 |
| requested_end不同，returned_end等于start | 此次原过滤返回了原位置 | 边界为何形成、是否存在其他路线 |
| filter允许不同端点，update后位置未变 | 更新前后位置确实不变 | 碰撞回滚、base-offset、执行模式或其他本地机制 |
| update后位置改变，但官方谓词为false | 运动发生且官方目标未满足 | 错误参考点、错误楼层、目标距离或其他条件 |
| 请求/返回或最终状态unavailable | 该字段没有可靠观测 | 不能用推测值补齐原因 |

位置差异和请求/返回比较是诊断值，不会变成Control eligibility、Formal admission或
benchmark truth。任何生产修复都应先有“声明允许但实现错误处理”的可复核案例；
真正的能力缺口应通过能力契约与独立验证解决，不能靠删除安全过滤或更换目标隐藏。
