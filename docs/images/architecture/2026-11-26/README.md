# 架构图片来源

当前生成状态：**0/16 张**。真实请求返回 HTTP 403：当前 Key 所在分组未启用生图。提示词已准备；开通权限并完成生成、逐图审核后才能发布图片版。

代码核对：2026-11-26 · `main@3c7136d6`。图文描述代码职责与协议边界；实际设备结果须另查原验收证据。

这组图片通过 imagegen CLI 调用现有网关的图片生成接口，显式请求 `gpt-image-2.6-sunburst`、high、1536×1024、PNG。接口实际结果、质量核对和图片摘要由生成记录保存；模型列表可见性不能替代真实生成成功。

| Figure | Prompt | Source evidence |
| --- | --- | --- |
| RoboGuide 系统总览 | [overview.zh.txt](prompts/overview.zh.txt) | [main.rs](../../../../apps/integration-server/src/main.rs), [lib.rs](../../../../core/orchestration/src/lib.rs), [lib.rs](../../../../core/control/src/lib.rs), [mod.rs](../../../../core/node-service/src/service/mod.rs) |
| 从对话到经过审查的计划 | [mission-planning.zh.txt](prompts/mission-planning.zh.txt) | [request_engine.py](../../../../mission/src/mission/request_engine.py), [task_tools.py](../../../../mission/src/roboguide_workbench/task_tools.py), [plan_validation.rs](../../../../apps/integration-server/src/application/plan_validation.rs) |
| 从候选到可靠执行 | [dispatch.zh.txt](prompts/dispatch.zh.txt) | [scheduling.rs](../../../../core/orchestration/src/mission/scheduling.rs), [coordination.rs](../../../../core/control/src/coordination.rs), [write_gate.rs](../../../../apps/integration-server/src/application/write_gate.rs), [admission.rs](../../../../core/node-service/src/engine/admission.rs) |
| 完成判定与资源释放 | [completion.zh.txt](prompts/completion.zh.txt) | [lifecycle.rs](../../../../core/orchestration/src/mission/lifecycle.rs), [outcomes.rs](../../../../apps/integration-server/src/application/outcomes.rs), [memory_use.rs](../../../../apps/integration-server/src/application/memory_use.rs) |
| State、Memory 与空间内容 | [state-memory.zh.txt](prompts/state-memory.zh.txt) | [state_record.rs](../../../../core/state/src/state_record.rs), [memory.rs](../../../../core/domain/src/memory.rs), [memory_preparation.rs](../../../../apps/integration-server/src/application/memory_preparation.rs), [shared_map_job.py](../../../../integrations/spatial-provider/roboguide_spatial_provider/shared_map_job.py) |
| 未知、停止与分级恢复 | [recovery.zh.txt](prompts/recovery.zh.txt) | [safety_stop.rs](../../../../apps/integration-server/src/application/safety_stop.rs), [recovery.rs](../../../../apps/integration-server/src/application/recovery.rs), [switching.rs](../../../../apps/integration-server/src/application/plan_revision/switching.rs) |
| 四种协作模式 | [cooperation.zh.txt](prompts/cooperation.zh.txt) | [manager.rs](../../../../core/runtime/src/relation/manager.rs), [server.rs](../../../../apps/integration-server/src/controller_http/server.rs), [sync_engine.py](../../../../integrations/sync-provider/roboguide_sync/sync_engine.py), [choreography_engine.py](../../../../integrations/sync-provider/roboguide_sync/choreography_engine.py) |
| 进程、存储与故障域 | [deployment.zh.txt](prompts/deployment.zh.txt) | [main.rs](../../../../apps/integration-server/src/main.rs), [lib.rs](../../../../core/controller-replication/src/lib.rs), [main.rs](../../../../apps/controller-replica/src/main.rs), [__main__.py](../../../../apps/recovery-tool/roboguide_recovery/__main__.py) |

[Machine-readable briefs](briefs.json) · [Generation records](generation-records.json)

## 继续生成

在调用进程中从本地配置取得 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`，不要复制凭据到本目录。开通当前分组的生图权限后，使用已安装 imagegen skill 的原版 CLI：

```bash
python /path/to/imagegen/scripts/image_gen.py generate \
  --model gpt-image-2.6-sunburst --size 1536x1024 --quality high \
  --output-format png --no-augment \
  --prompt-file docs/images/architecture/2026-11-26/prompts/overview.zh.txt \
  --out docs/images/architecture/2026-11-26/overview.zh.png
```

型号以[官方模型页](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst)和实际接口权限为依据。先逐项核对总览图，再按 briefs 生成其余语言和主题。核对通过后更新 `website/diagram-assets.json` 的真实图片摘要、尺寸和审核状态，并补充实际生成记录；不能将提示词准备或模型列表可见记作完成。

实际生成图必须经文字、连线和边界逐项核对。生成记录中 requested_model 是显式请求型号；只有接口实际返回模型身份时才记录 returned_model，不将路由名称当作独立模型证明。
