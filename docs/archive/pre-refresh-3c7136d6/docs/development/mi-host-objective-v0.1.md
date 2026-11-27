# MI 宿主目标格式：接入与验证

主线整合的显式格式见 [ADR-0255](../decisions/0255-host-owned-mission-objective.md)。新格式消除模型复述
`mission.objective` 的要求；原目标和用户约束仍完整输入模型，所有生成动作仍须正常验证和 Review。

## 获取独立工作树

在现有 RoboGuide 仓库中执行：

```bash
git fetch origin
git worktree add -b verify/mi-host-objective ../roboguide-mi-host-objective origin/main
cd ../roboguide-mi-host-objective
uv sync --frozen
uv run pytest -q mission/tests/test_host_objective.py
```

使用包含 ADR-0255 的 main 版本；原独立分支仅作为历史来源保留。
测试只使用本地协议 fixture。它验证软件格式和拒绝边界，不调用真实模型或启动模拟器。

## 显式配置

复制原试跑配置到新路径，在其已有 `[mission]` 表中加入一行：

```toml
[mission]
provider_plan_profile = "host-objective-v1"
```

保留原来的其他 mission、模型、Review、预算及 schema 设置。不要只留下上述两行覆盖整个文件。
仓库提供 `tools/e2-generic/mission-dag-host-objective-gpt-6.1-sol.toml`，它与原 generic DAG
Sol 配置仅相差这一选项。1031 物理配置仍应从其原配置复制，以保留该批次参数。
未填写该字段的配置继续使用 `echo-objective-v1`。两种格式不能根据模型返回内容自动切换。

运行前验证实际配置：

```bash
uv run python - <<'PY'
from pathlib import Path
import mission
from mission.config import load_settings
# 替换成刚复制并修改的配置路径。
settings = load_settings(Path('/absolute/path/mission-host-objective.toml'), repository_root=Path.cwd())
print('MI source:', mission.__file__)
print('profile:', settings.provider_plan_profile)
assert settings.provider_plan_profile == 'host-objective-v1'
PY
```

1031 的 `plan_and_record_physical.py`、`run_dag_physical.py` 只在包中登记了摘要，未包含源码。
继续使用服务器实际维护的物理 runner，将其 `--repo`、导入路径和 `--config` 指向新工作树与
新配置；检查 `PYTHONPATH`，避免仍导入旧 checkout 的 `mission`。本分支未替换那些外部脚本。
若外部脚本自行构造 MissionSettings，也要传入该显式 profile；读取新 TOML 的标准
`load_settings()` 已支持它。仅更新代码而不启用配置，会继续运行旧规则。

## 原始证据验收

用新输出目录分别验证苹果、烤串和咖啡杯，保留 1031 原失败记录。

1. 原始 Planner/Repairer 请求的 output schema 必须要求
   `provider_profile="host-objective-v1"`，其 mission 属性中没有 objective。
   输入 `grounded_intent.objective`、constraints、assumptions 仍是本轮原值。
2. 原始模型草案含正确 profile、正确 mission.id，且没有 mission.objective。
   即使相同原文或 null 回显该字段，也应拒绝，不能覆盖后放行。
3. 组装后的 draft/mission-plan.json 必须保留完整原 objective，不能只留摘要或重写内容。
   Reviewer 请求必须含该完整计划和原始 grounded_intent、同一份 grounding。
4. 若用了标准 Mission Service，provider provenance 的 plan_assembly 关联 raw/assembled/intent
   摘要。大草案可明确标 truncated；原 trace 空间紧张时可省略可选组装观察，
   不减少原有模型调用额度，也不删除已持久化历史。缺失观察保持不可用。E2 recorder
   的原始 request/response 文件另行保留。
5. schema、未知操作、错误对象/目标条件、来源漂移和执行失败仍须原校验处理。
   有明确 authoritative semantic evidence 的部署按原精确目标门禁检查；纯自然语言的动作
   语义仍依赖独立 Review 和执行验证，不能从目标字段正确推断动作正确。

不得将本分支启用后的结果补写为 1031 成功。记录原调用次数、耗时、token、生成入口、拒绝阶段、
原始响应、Review 和实际执行结果；即使文本格式通过，物理放置与持有状态失败仍需各自诊断。

## 通用接入责任

`objective` 应只放已确认目标，用户约束放 constraints。动作知识走 Catalog/部署声明，场景事实
走有来源的 grounding，执行反馈走有原权限和预算的 runtime replanning context。
本次宿主组装解决目标复述负担，不会自动把混装长文本解析成可信的结构化上下文。

## 重启身份与提示词范围

运行期 worker 的原 prompt identity 包含启用的 host-objective 附加资产。该资产改变后，
旧 work 必须被原 identity drift 门禁拒绝；不能用新 Prompt 继续旧调用或重置旧预算。
附加 Prompt 仅禁止 mission.objective；Role execution_intent.objective 仍是必要的动作语义。
