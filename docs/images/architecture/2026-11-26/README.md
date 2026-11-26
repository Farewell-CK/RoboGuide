# 架构图片来源

代码核对：2026-11-26 · `main@3c7136d6`。16 张中英文 PNG 均已通过实际生图/图片编辑接口生成并完成文字、主流程与职责边界核对。图中设备为示例类别，具体协议、异常与证据以正文及源码为准。

整体视觉参考 [原始 V2 图](../../roboguide-v2-overall-architecture.png) 与 [V1.1 图](../../distributed-embodied-ai-os-architecture-v1.1.png)：彩色分层、紧凑模块、工程图标和局部说明。总体框架与七个专题分别表达结构和流程；旧图保留原字节。

选定图片由 imagegen CLI 通过图像生成/编辑 API 实际取得，明确请求 `gpt-image-2.6-sunburst`、high、1536×1024 PNG。初始网关曾返回 403，后来切换到单独配置的生图网关后成功；未改变 Codex 配置。CLI 未返回可核实的实际模型身份，因此记录只声明 requested_model。

| 图片 | PNG 原图 | 原始规格 | 最终生成提示词 |
| --- | --- | --- | --- |
| RoboGuide 总体框架 | [原图](overview.zh.png) | [规格](prompts/framework.zh.reference-v2.txt) | [提示词](prompts/framework.zh.reference-v2.fix-01.txt) |
| 从对话到经过审查的计划 | [原图](mission-planning.zh.png) | [规格](prompts/mission-planning.zh.reference-v2.txt) | [提示词](prompts/mission-planning.zh.reference-v2.fix-02.txt) |
| 从候选到可靠执行 | [原图](dispatch.zh.png) | [规格](prompts/dispatch.zh.reference-v2.txt) | [提示词](prompts/dispatch.zh.reference-v2.fix-02.txt) |
| 完成判定与资源释放 | [原图](completion.zh.png) | [规格](prompts/completion.zh.reference-v2.txt) | [提示词](prompts/completion.zh.reference-v2.fix-01.txt) |
| State、Memory 与空间内容 | [原图](state-memory.zh.png) | [规格](prompts/state-memory.zh.reference-v2.txt) | [提示词](prompts/state-memory.zh.reference-v2.fix-01.txt) |
| 未知、停止与分级恢复 | [原图](recovery.zh.png) | [规格](prompts/recovery.zh.reference-v2.txt) | [提示词](prompts/recovery.zh.reference-v2.fix-03.txt) |
| 四种协作模式 | [原图](cooperation.zh.png) | [规格](prompts/cooperation.zh.reference-v2.txt) | [提示词](prompts/cooperation.zh.reference-v2.fix-01.txt) |
| 进程、存储与故障域 | [原图](deployment.zh.png) | [规格](prompts/deployment.zh.reference-v2.txt) | [提示词](prompts/deployment.zh.reference-v2.txt) |

[完整规格与源码索引](briefs.json) · [实际生成和编辑记录](generation-records.json) · [当前图册](../../../architecture/v2/current-architecture.md)

## 更新图片

使用已安装 imagegen CLI 和仅供本次调用的本地 API 凭据，参考原版图或已审核的中文图。实际选定的编辑步骤、输入输出摘要和提示词保留在生成记录中；试画 PNG 留在本地工作目录，正式仓库仅保存最终图和原始历史参考图。

```bash
python /path/to/imagegen/scripts/image_gen.py edit \
  --model gpt-image-2.6-sunburst --size 1536x1024 --quality high \
  --output-format png --no-augment \
  --image docs/images/roboguide-v2-overall-architecture.png \
  --image docs/images/distributed-embodied-ai-os-architecture-v1.1.png \
  --prompt-file docs/images/architecture/2026-11-26/prompts/framework.zh.reference-v2.txt \
  --out output/imagegen/framework-draft.png
```

型号说明：[OpenAI 官方模型页](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst)。重新生成后逐图审核，更新 `website/diagram-assets.json`、`briefs.json` 与 `generation-records.json` 的确切清单和摘要，再执行正常文档构建。不得把草稿或接口声称当作源码证明。
