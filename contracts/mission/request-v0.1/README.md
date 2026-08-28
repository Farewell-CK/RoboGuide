# Mission Request Contract v0.1

外部入口 `POST /v1/mission-requests` 只接受：

```json
{"instruction":"让一只可建图的机器狗建立地图并共享给另一只狗"}
```

RequestId、MissionId、Task/Context/Role identity 均由 RoboGuide 生成。用户通过
`POST /v1/mission-requests/{id}/messages` 提交澄清文本；命中部署风险策略时，通过
`POST /v1/mission-requests/{id}/approve` 提交当前 `draft_revision` 与 `draft_digest`。

[`mission-request.schema.json`](mission-request.schema.json) 定义 GET/status 的持久化投影。
该投影属于 Mission Intelligence deliberation evidence，不是 MissionPlan、Execution Group、
Runtime checkpoint 或 State Node projection。
