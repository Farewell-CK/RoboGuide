# Architecture image provenance

Generation status: **0/16 images**. The actual request returned HTTP 403 because image generation is not enabled for the key's group. Prompts are ready; publication requires restored access, successful generation and visual review of each image.

Code reviewed: 2026-11-26 · `main@3c7136d6`. Figures describe implemented responsibilities and protocol boundaries; device outcomes require original acceptance evidence.

These assets use the imagegen CLI and the configured gateway’s image generation endpoint, explicitly requesting `gpt-image-2.6-sunburst`, high quality, 1536×1024 PNG. Generation records retain actual outcomes, review and image hashes; model-list visibility is not generation success.

| Figure | Prompt | Source evidence |
| --- | --- | --- |
| RoboGuide system overview | [overview.en.txt](prompts/overview.en.txt) | [main.rs](../../../../apps/integration-server/src/main.rs), [lib.rs](../../../../core/orchestration/src/lib.rs), [lib.rs](../../../../core/control/src/lib.rs), [mod.rs](../../../../core/node-service/src/service/mod.rs) |
| From conversation to a reviewed plan | [mission-planning.en.txt](prompts/mission-planning.en.txt) | [request_engine.py](../../../../mission/src/mission/request_engine.py), [task_tools.py](../../../../mission/src/roboguide_workbench/task_tools.py), [plan_validation.rs](../../../../apps/integration-server/src/application/plan_validation.rs) |
| From candidates to durable execution | [dispatch.en.txt](prompts/dispatch.en.txt) | [scheduling.rs](../../../../core/orchestration/src/mission/scheduling.rs), [coordination.rs](../../../../core/control/src/coordination.rs), [write_gate.rs](../../../../apps/integration-server/src/application/write_gate.rs), [admission.rs](../../../../core/node-service/src/engine/admission.rs) |
| Satisfaction and resource release | [completion.en.txt](prompts/completion.en.txt) | [lifecycle.rs](../../../../core/orchestration/src/mission/lifecycle.rs), [outcomes.rs](../../../../apps/integration-server/src/application/outcomes.rs), [memory_use.rs](../../../../apps/integration-server/src/application/memory_use.rs) |
| State, Memory and spatial content | [state-memory.en.txt](prompts/state-memory.en.txt) | [state_record.rs](../../../../core/state/src/state_record.rs), [memory.rs](../../../../core/domain/src/memory.rs), [memory_preparation.rs](../../../../apps/integration-server/src/application/memory_preparation.rs), [shared_map_job.py](../../../../integrations/spatial-provider/roboguide_spatial_provider/shared_map_job.py) |
| Unknown, stopping and recovery | [recovery.en.txt](prompts/recovery.en.txt) | [safety_stop.rs](../../../../apps/integration-server/src/application/safety_stop.rs), [recovery.rs](../../../../apps/integration-server/src/application/recovery.rs), [switching.rs](../../../../apps/integration-server/src/application/plan_revision/switching.rs) |
| Four cooperation modes | [cooperation.en.txt](prompts/cooperation.en.txt) | [manager.rs](../../../../core/runtime/src/relation/manager.rs), [server.rs](../../../../apps/integration-server/src/controller_http/server.rs), [sync_engine.py](../../../../integrations/sync-provider/roboguide_sync/sync_engine.py), [choreography_engine.py](../../../../integrations/sync-provider/roboguide_sync/choreography_engine.py) |
| Processes, storage and failure domains | [deployment.en.txt](prompts/deployment.en.txt) | [main.rs](../../../../apps/integration-server/src/main.rs), [lib.rs](../../../../core/controller-replication/src/lib.rs), [main.rs](../../../../apps/controller-replica/src/main.rs), [__main__.py](../../../../apps/recovery-tool/roboguide_recovery/__main__.py) |

[Machine-readable briefs](briefs.json) · [Generation records](generation-records.json)

## Resume generation

Resolve `OPENAI_API_KEY` and `OPENAI_BASE_URL` from local configuration in the calling process;
never copy credentials here. Once the current group can generate images, use the installed,
unmodified imagegen skill CLI:

```bash
python /path/to/imagegen/scripts/image_gen.py generate \
  --model gpt-image-2.6-sunburst --size 1536x1024 --quality high \
  --output-format png --no-augment \
  --prompt-file docs/images/architecture/2026-11-26/prompts/overview.zh.txt \
  --out docs/images/architecture/2026-11-26/overview.zh.png
```

The requested model binds the [official model page](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst)
and actual endpoint access. Review the overview before producing the other topics and locale.
After visual review, update actual PNG hashes, dimensions and review status in
`website/diagram-assets.json`, and record actual outcomes. Ready prompts and model-list visibility
are not successful generation.

Generated figures require label, edge and boundary review. requested_model records the explicit request; returned_model is recorded only when the API actually returns model identity, not inferred from a routing name.
