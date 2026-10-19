# Node catalog v0.15：Memory 与原执行代次的统一合同

本目录的 wire 使用 Node Protocol v0.4，明确协商 Node Contract v0.12 与 config v0.13。
它包含此前 v0.11 的 Memory preparation/use/只读 revoke query；旧合同不获得新停止语义。

Execute 保留 tags 8–10 的 preparation ID、digest 与 semantic requirements，新增 tag 11
传输非零 Runtime attempt generation。Controller 在生成 preparation 时冻结相同 generation；
Node 在任何准备/执行副作用前核对原 epoch、generation、prepared invocation 和 current-use。
重复命令不可替换 generation 或 renew 原预算；普通旧合同保持原来的 invocation digest。

配置中的停止声明只允许显式 v0.13，不从 metadata 自动改变旧配置合同。旧 v0.8 仍表示
registered map producer；原独立停止分支曾使用的 v0.8/tag 8 不作为当前兼容合同。
共享 Controller wrapper v41 与 Node journal v12 拒绝旧 writer，含进程退出权威的歧义 fork
checkpoint 拒绝自动迁移。详见 [ADR-0111](../../../docs/decisions/0111-branch-integration-and-authority-version-fences.md)。

Local EAIOS 仍拥有即时执行与最终安全权威。safe-state v0.3 的原软件进程退出既不等于
Cancel receipt，也不证明设备停止；Runtime 原 Completed/Failed 不改写为 Cancelled。
