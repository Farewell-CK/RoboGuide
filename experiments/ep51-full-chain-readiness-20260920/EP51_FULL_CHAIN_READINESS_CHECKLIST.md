# Episode51 完整链路诊断 Readiness Checklist

## 已完成的静态与离线检查

- [x] `origin/main` 固定为 `28a3dcda5eb44d48df83700d13b4e0b1b601ea97`。
- [x] 在独立 worktree/branch 工作，未触碰用户未跟踪实验目录。
- [x] Planner、Reviewer、Repairer Prompt 均来自该 main，并记录 SHA256。
- [x] frozen input、dataset revision/digest、scene、episode=51、seed=40 已逐项核对。
- [x] runner 是 production MI → Control → Node → original Stage2 → Habitat 路径，无 B2 plan fallback。
- [x] runner 对一个 instruction 只提交一次；timeout 后恢复同一 request_id，不重提 Request。
- [x] `space:1` 来自 deployment execution profile，Controller 仍是 commit authority。
- [x] 历史 node/slot assignments、assignment arrival、3,000-step outcome和官方 False 已用原始证据核对。
- [x] Mission terminal 与官方 PDDL success 使用不同证据 authority。
- [x] 完整 Controller events 有界分页与归档不完整的独立归因仍存在。
- [x] physical diagnostics 默认关闭，只在 flag=`1` 时启用。
- [x] 修复逐 step 同步文件 I/O和 evidence error 向物理主循环传播的问题。
- [x] reset/step/terminal 记录失败均 fail-soft，并记录 unavailable/loss stats。
- [x] targeted、Python、Rust 与质量门禁已执行；未调用 Provider 或 Habitat。

## 下一次授权后、启动前必须重新通过

- [ ] 使用干净 detached worktree `db114d610fe89076ba4c3166752df3b01d85421f`。
- [ ] input SHA256 = `1026761241f906ca2a35ab567082ab3d281d76524d5c8ab38a2c8522bbaf87c6`。
- [ ] dataset SHA256 = `5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca`。
- [ ] execution profile SHA256 = `2e0aaa5857cb208050241df0ccc04315c09bfdf2d97dab141e56c9e79535b504`。
- [ ] runner SHA256 = `1e326789fb1a0e113cc02676f3aa2839320523c0f9248eb2ae5027bf9b710f2a`。
- [ ] EMOS HEAD = `e9501db45d634b087bf5d1a14228266685e8feeb`，tracked diff SHA256 未变。
- [ ] GPU 1 容量充足；不干扰 GPU 0 和外部实验进程。
- [ ] 8070、25060、28060、28090、28100、28102 均空闲。
- [ ] Habitat Conda imports 和 CUDA 可用。
- [ ] 必要 Provider 环境变量非空；只检查存在性，不打印内容。
- [ ] Rust binaries 从固定 commit 构建成功。
- [ ] run directory 全新且有足够磁盘空间。
- [ ] diagnostics=`1`、MI observation budget=1800 已显式冻结。

## 运行后证据验收

- [ ] 只有一个 request_id；store、timing、wait outcome、request record 一致。
- [ ] 保存实际 final MissionPlan、review history及两个官方目标引用。
- [ ] 保存每个 Task 的 `space:1`、node/resource commitment、execution/endpoint mapping。
- [ ] `diagnostics-initial.json` 有实际 post-reset position/rotation、goal entity positions和逐谓词值。
- [ ] `diagnostics-steps.jsonl` 覆盖真实 step，包含 pose/rotation、skill counter、finished sensor和谓词值。
- [ ] `diagnostics-terminal.json` 有真正终态 pose、逐谓词值、官方 metrics和 collection stats。
- [ ] action trace 与 shared-world summary 的 write/dropped counters 为可接受值；否则标记证据不完整。
- [ ] 保存 Stage2 Prompt/tool/usage 证据；缺少完整 Provider body时明确标注能力边界。
- [ ] Controller event archive 状态为 complete；否则单独归因为 evidence collector。
- [ ] Mission outcome 与官方 `pddl_success` 分开报告。
- [ ] 不因失败追加调用、换 seed、延长 steps、修改目标或注入历史计划。

## 已知观测边界

- [ ] 不把 seed40 当作相同实际初态；只比较 reset 快照。
- [ ] 不从停滞推断场景碰撞；当前没有逐 agent 场景接触流。
- [ ] 不把欧氏距离当作 PathFinder geodesic/path evidence。
- [ ] 不把 inferred skill-exit candidates 当作原生 Stage2 exit event。
- [ ] 不从 local Completed 推断某个官方谓词为 True。
- [ ] 不从联合 `pddl_success=False` 推断两个谓词都为 False。

