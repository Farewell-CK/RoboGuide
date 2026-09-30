# 场景（对照版）：四个节点全部声明 Robonix OS — `hcore-homogeneous-v0.2`

**这是 `hcore-heterogeneous-v0.1` 的同构对照组。** 世界、本体、任务、脚本完全一样，
只改一件事：四个节点上报的 **Local EAIOS 名字全部改成 `robonix-os`**。

## 为什么要做这一版

异构版证明了"四套不同 OS 能一起被调度"。但它留了一个可能的反驳：

> 会不会 RoboGuide 其实是靠 **OS 名字不同** 来区分节点的？
> 如果四个系统同名，控制面是不是就分不清谁该干什么了？

这一版把 OS 名字抹平（四个都叫 `robonix-os`），本体/能力集/运动学一个字不改，
看控制面还能不能把每条任务派到正确的本体上。

## 改了什么（相对 v0.1 只有两处）

| 改动 | 位置 | 说明 |
|---|---|---|
| 节点声明的 OS 名 | 4 个 `node-*.toml` 的 `runtime_name = "robonix-os"` | 控制面看到的四套系统变成同一种 |
| facade 上报的 OS 名 | 启动时加 `--runtime-name robonix-os` | 让本地 facade 的 `/v1/describe` 与日志也一致 |

**没改的**：MJCF 世界、四个本体的运动学、能力集、`missions/*.json`、
`run-step2-mission.sh`、五个 Mission 的内容与顺序。
任务里的约束是 `capability`（如 `mobility.navigate@v1`）与属性约束
（如 `push-capable = false`），**没有任何一处按 OS 名字做约束** —— 这正是要验证的点。

## 怎么跑

```bash
python3 scenarios/hcore-homogeneous-v0.2/collect-metrics.py 3
```

产出三张 CSV：

```
results/metrics-raw.csv        逐条 execution（带 variant 列）
results/metrics-summary.csv    逐指标（带 variant 列，含同构独有的 M12）
results/metrics-compare.csv    与异构版 v0.1 并排对照
```

## 指标差异（相对异构版）

- **M1~M11 与异构版同口径**，可以直接并排比：接入成功率、任务成功率、
  下发/反馈延迟、开销、交接成功率、故障遏制。
- **M12「同 OS 下的路由正确率」是这一版独有**：统计每次 execution 实际落到的本体
  是否等于 capability 约束期望的本体。若仍为 100%，说明调度只依赖
  capability/region，不依赖 OS 名字。

## 预期与解读

同构版应当与异构版**结果一致**（成功率、交接、故障遏制都不变）。因为 RoboGuide
的 Match 走的是 canonical capability 契约，OS 名字只是节点自报的元数据。
如果同构版出现"任务落到错误的本体"或"匹配不到节点"，才说明控制面偷偷依赖了 OS 名字。
