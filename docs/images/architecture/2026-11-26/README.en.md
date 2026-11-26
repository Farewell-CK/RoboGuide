# Architecture image provenance

Code reviewed: 2026-11-26 · `main@3c7136d6`. All 16 Chinese/English PNGs have actual image-API outputs and visual review of labels, principal flows and ownership boundaries. Devices are illustrative categories. The accompanying guides and source files define exact contracts, exceptions and evidence.

The visual references are the [original V2 plate](../../roboguide-v2-overall-architecture.png) and [V1.1 plate](../../distributed-embodied-ai-os-architecture-v1.1.png): compact colored planes, engineering pictograms and technical annotations. One framework plate and seven topic diagrams distinguish structure from flows. Historical image bytes remain intact.

The installed imagegen CLI requested `gpt-image-2.6-sunburst`, high quality, 1536×1024 PNG through the image APIs. After the first gateway returned HTTP 403, a separately configured image gateway succeeded without changing Codex configuration. The CLI does not expose a verifiable returned model identity; the records identify the requested model only.

| Figure | PNG | Initial specification | Final operation prompt |
| --- | --- | --- | --- |
| RoboGuide system framework | [Full image](overview.en.png) | [Spec](prompts/framework.en.reference-v2.txt) | [Prompt](prompts/framework.en.reference-v2.txt) |
| From conversation to a reviewed plan | [Full image](mission-planning.en.png) | [Spec](prompts/mission-planning.en.reference-v2.txt) | [Prompt](prompts/mission-planning.en.reference-v2.txt) |
| From candidates to durable execution | [Full image](dispatch.en.png) | [Spec](prompts/dispatch.en.reference-v2.txt) | [Prompt](prompts/dispatch.en.reference-v2.fix-02.txt) |
| Satisfaction and resource release | [Full image](completion.en.png) | [Spec](prompts/completion.en.reference-v2.txt) | [Prompt](prompts/completion.en.reference-v2.fix-01.txt) |
| State, Memory and spatial content | [Full image](state-memory.en.png) | [Spec](prompts/state-memory.en.reference-v2.txt) | [Prompt](prompts/state-memory.en.reference-v2.fix-01.txt) |
| Unknown, stopping and recovery | [Full image](recovery.en.png) | [Spec](prompts/recovery.en.reference-v2.txt) | [Prompt](prompts/recovery.en.reference-v2.fix-01.txt) |
| Four cooperation modes | [Full image](cooperation.en.png) | [Spec](prompts/cooperation.en.reference-v2.txt) | [Prompt](prompts/cooperation.en.reference-v2.txt) |
| Processes, storage and failure domains | [Full image](deployment.en.png) | [Spec](prompts/deployment.en.reference-v2.txt) | [Prompt](prompts/deployment.en.reference-v2.txt) |

[Briefs and source evidence](briefs.json) · [Actual generation and edit records](generation-records.json) · [Current atlas](../../../architecture/v2/current-architecture.en.md)

## Updating figures

Use the installed imagegen CLI with credentials supplied only to the calling process, and use the original plate or a reviewed Chinese figure as a reference. The records bind exact prompts and selected input/output digests; intermediate variants stay in local working files. The repository retains the final figures and original historical reference images.

```bash
python /path/to/imagegen/scripts/image_gen.py edit \
  --model gpt-image-2.6-sunburst --size 1536x1024 --quality high \
  --output-format png --no-augment \
  --image docs/images/roboguide-v2-overall-architecture.png \
  --image docs/images/distributed-embodied-ai-os-architecture-v1.1.png \
  --prompt-file docs/images/architecture/2026-11-26/prompts/framework.zh.reference-v2.txt \
  --out output/imagegen/framework-draft.png
```

Model information: [official OpenAI model page](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst). Review new images, update the exact manifest, briefs and generation records, and run the normal publication checks. Generated diagrams do not establish runtime or physical acceptance.
