# MissionPlan v0.9

继承 v0.8 的 Actor/实体语义，Role requirements 可增加至多 8 个 `memories`。每项只包含
不可变 selector、kind、payload_schema 和 media_type，不携带 Node、consumer provider 或路径。
Control 根据当前实际注册决定接收者；缺少准备/实际导入/当前 availability 时禁止 Execute。
旧 v0.8 及以前拒绝新字段。Grounding 仍须来自已接纳来源，provider 输出不能发明 memory。

这是正在实施的合同；完整正式协议和实际进程验收前不代表功能完成。
