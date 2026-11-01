# MI 请求不传输出 token 上限 v0.3

按当前部署要求，生产 Responses 公共客户端的全部路径（Interpreter、Planner、Reviewer、
Repairer、结构恢复和运行期重规划）不发送 max_output_tokens、max_tokens、
max_completion_tokens 或 max_token。LlmSettings不再有该覆盖项；现有配置、E1场景、
E2配置及模型探测脚本已移除主动设置。旧本地TOML中的同名键不再被读取或发送。
Provider自身的默认限制与返回用量仍由Provider决定，不把“未发送上限”称为无限生成保证。

生产Prompt、模型选择、结构化schema、恢复轮数和socket timeout未因该项改变。
历史请求、用量账本、导入器和测试历史数据继续保留原字段，以便准确复核旧运行。

## 实验身份

新freeze生成 `roboguide.mi-ablation-manifest/v0.2`，记录
`provider.output_token_limit_policy=omitted`，不含token上限。当前执行器拒绝旧v0.1
manifest或带上限参数的provider配置，防止同一份输入身份下悄悄改变请求条件。
旧实验使用其原始提交和配置复算，不能改写历史manifest。

[v0.1](mi-ablation-v0.1.md)、[v0.2](mi-ablation-v0.2.md)中的16384是旧试验的条件，
保留其历史含义。按新要求续跑的未开始案例使用新的代码/manifest/输出目录；
结果与旧条件分列，不能将前后候选混成同配置的正式成功率或声称参数唯一因果。
每个新案例内部A/B/C/D使用相同的不传输出上限策略，原D逻辑调用匹配规则保持。

## 传输异常与已关闭的不完整响应

已归档完整HTTP响应但status=incomplete的案例保留unavailable，不能截取部分JSON或
删除重复空白伪造合法计划。可在明确版本化的本地采集规则下继续其他未开始案例，
不自动重试同一不完整响应。超时、回执未知和身份错误保持停止与禁止重放。
已授权的HTTP502重试每次单独留证，逻辑工作数与实际HTTP次数、token、耗时分别报告。

不传上限并不证明旧输出退化已解决；其旧请求明确16384，而Provider报告128000输出，
主体几乎全为空白。应用参数正确序列化与后端实际执行是两个层次，缺少上游日志时
不指定是模型、代理转发还是用量报告的独立根因。新试验仍保存完整原响应。
