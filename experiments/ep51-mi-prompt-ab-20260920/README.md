# Episode51 MI Prompt A/B 诊断报告

按用户后续发布指令归档；不是正式 E1 benchmark。本分支只增加报告和静态证据，不改变生产代码、Prompt、协议或 main。

- [中文报告](EP51_MI_PROMPT_AB_REPORT.md)
- [结构化摘要](EP51_MI_PROMPT_AB_SUMMARY.json)
- [预注册 manifest](manifest.json)
- [逐次执行结果](execution-summary.json)
- [离线证据复核](offline-evidence-analysis.json)

旧 Prompt 使用 1 次初始调用和 2 次恢复，最终虽通过结构校验，但新增了没有任务依据的 requires-active 关系。
新 Prompt 首次生成 independent，保留两个目标及 space:1。新 Prompt 的真实恢复能力没有在本次触发，不能宣称已验证。

`A/`、`B/` 含实际发送内容、完整 JSON 响应、草案、归一化结果、校验结果、恢复反馈和最终计划。
`frozen/` 是实验输入快照，不是当前服务使用的配置或 Prompt；所有原始证据文件逐字节复制，未补造模型输出。
`SOURCE_INTEGRITY.json` 记录本机来源与逐文件原始摘要；`SHA256SUMS` 验证本次发布文件。

原始目录：`/data/workspace/code/roboguide-ep51-mi-prompt-ab-20260920T124627Z`。
仅依赖本机路径的 `study.py`、`analyze.py`、`write_report.py` 不随报告发布，其中 study.py 包含本机凭据装载流程。
这些文件没有在本次发布中执行；所有原始文件仍保留在本机。报告中的源码绝对路径是当时调查位置。
报告发布副本仅新增发布说明、调整本机脚本链接措辞；实验数据、结论及局限保持不变。

复核发布文件：在本目录运行 `sha256sum --check SHA256SUMS`。
预注册的单轮实验已结束；本次发布不授权追加 Provider 调用或启动任何执行服务。
