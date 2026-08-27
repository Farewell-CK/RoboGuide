# Spatial Memory Contract v0.1

`map-manifest.schema.json` 定义 Spatial Memory 的跨进程 manifest。它描述不可变地图
revision 的 provenance、bytes 完整性和固定物理 anchor；它不包含地图二进制。

`map_id`、`revision_id` 与可选的 `parent_revision_id` 是逻辑引用。为保证它们可以直接作为
Artifact HTTP 的单个 path segment，三者只接受 ASCII grammar
`[A-Za-z0-9][A-Za-z0-9._:-]*`：首字符必须是字母或数字，后续字符才可额外使用
`.`、`_`、`:`、`-`。`SpatialAnchorId` 不承担 artifact 寻址职责，不受这条规则影响。
`content_digest` 是 `sha256:<64 lowercase hex>`。发布后 manifest 不可覆盖。Node replica
状态（staged/imported/verified/rejected）属于 State projection，不写回 manifest。

v0.1 的 artifact data plane 是独立 streaming HTTP；Node Protocol v0.2 不变，地图内容不
进入 gRPC message。Consumer 必须显式提供预分配的 map/revision 引用，不能依赖 Runtime
动态替换 producer output。
