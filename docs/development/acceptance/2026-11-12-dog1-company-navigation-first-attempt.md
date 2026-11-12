# 2026-11-12 狗1公司门口导航：目标解析失败

本轮导航未通过。用户明确选择公司门口并允许预检通过后一次导航，到达停稳，
禁止自动重试、返回和趴下。实际MI→RG→狗1Robonix委派已发生，但本机在解析目标时
使用了缺少末尾字符的区域引用，因此没有生成任何导航调用。原失败保留，不自动重试。

## 当前目标与接入准备

狗1自身当前Scene中，公司门口annotation为`anno.2637f95d`，map为`1101_visual_dog2`。
原definition_json UTF-8为3149字节，SHA与原region_source_digest一致：
`sha256:ea3ac063819dfa39461fa9350eba141b60f875c602a1f732bc11f3e5f0c1dc49`。
没有借狗2坐标，未在RG中选择polygon中心点或yaw。新鲜姿态standing、电量约72%，
本机Pilot READY，Mapping有图且地图标签一致；这些不证明定位质量、地图原子性或到达。

原固定双狗预检继续保持原适用范围。此次在lab操作脚本中显式组合原source-r4的
identity/World Scene/chassis/position validators，限定狗1公司门口，扣Controller
留存和全部本地读取/校验预算；独立复核通过。没有修改全局ASSIGNMENTS或把狗2缺失
伪装成原双狗预检通过，也不称它为通用MI/Core准入。

复用已验收generic verifier enrollment/publisher，新增独立精确source
`onsite-dog1-company-20261112` revision1；原`onsite-patrol-arrival`逐值不变。
受限principal仍为`rg-patrol-verifier`、tenant local。新谓词：

`onsite-arrival/v0.1 entity-lite3-dog1 arrived-and-stopped at 公司门口 target-reference-sha256:28ad8b320dd39fe5bd7fc50ad6834e581caf223e739c5a3e71ad04ddb8867a7b`

源注册通过权限检查，registry变为revision2，未发布正证据、未生成Execute。
从原基础catalog和认证registry生成新目录，保留旧目录/配置，再重启现有MI服务加载；
Controller/Node/Robonix未重启，没有新增守护进程。MI catalog规范摘要为
`sha256:7e475cfe918ae722dfa206f065c150a2334f1ff91b98005eb21e3939ec272d11`。

## 真实计划与审批

- Request：`request-444ff5bbacb44d8888559eab5c2b31d1`。
- Mission：`mission-ee5787eb3cf44cb09f8742b3722b5e67`。
- 草稿revision1，digest `sha256:a9f024c4607eef372e508c16aea783472eac544cd3b34bcf705ec5d6aeaea3cf`。
- 固定Actor dog1→entity-lite3-dog1，单independent Context navigation、单Task
  arrive-company、单Role navigate-role，task.delegate@v1/profile=local-task-v1，Space1/task。
- satisfaction为verifier-evidence，expected_effect逐字等于上述exactpredicate，
  observation.patrol.verify-arrival@v1、max_evidence_age_ms=5000。

实际MI Reviewer和独立复核通过，审批前原源码validators重新读取实时原数据，
确认standing/电量、地图与原Scene引用、路由和能力、Unknown0、allocations为空。
只审批原revision/digest并形成一次Commit/Bind和Execute，未直接调用底盘/导航接口。
本机objective完整保存现场条件及一次导航限制，导航How和安全检查由Robonix决定。
本机完成不等待人工verifier，API成功不能冒充独立到达证明，没有完成等待环。

## 原始失败链路

唯一attempt：
`attempt-46:group-mission-ee5787eb3cf44cb09f8742b3722b5e67-14:arrive-company-13:navigate-role-1`。
唯一native session：`98eff716-d880-4331-82f4-50d1adc9b48e`。
适配器29条事件，dispatched1、cancel_requested0，最终FAILED：

1. 原本机request.objective完整包含`anno.2637f95d`，SHA
   `6278dc5df6249939953ca98e2594b3c6427a9928dd75ad4990fa816acf405f57`。
2. seq13，真实Scene `list_regions`执行结果，call `41db-4:3`，公司门口稳定id为
   `scene.region.anno.2637f95d`，stale=false，map_id=1101_visual_dog2。
3. seq21，本机Plan41db-5的call `41db-5:0`，contract
   `robonix/system/scene/goal_region`，原参数却是
   `{"region_id":"scene.region.anno.2637f95"}`，少了末尾`d`。
4. seq23同call的RPC/leaf success=true、error为空，但语义结果reachable=false，
   reason为unknown region reference，并给出正确公司门口ID。RPC成功不等于目标可达。
5. seq28 T1 failed、seq29 final_text；完整计划没有导航call，也没有站立/其他动作调用。

最早Controller TaskFailed事件2540047，时间1794449077068；随后Mission/Task/attempt
均Failed，正常释放该槽，allocations为空、Unknown0。最新RG/native报告仍standing，
电量56%左右；没有将位置未变当作正式物理停止证明，未请求到达确认或发布到达证据。

所以这次可定位到**Robonix本机生成的工具参数少字符**，不是RG原目标传输丢字符。
不修改原结果来宣布成功，也不临时改模型、Scene解析器或底盘程序。本轮一次性运动
授权已消耗；若重发导航，需新草稿、最新预检及新一轮现场授权。

## 原证据

Windows：`C:/workspace/DEAIOS/lab/rg-dog1-navigation-20261112`。
ThinkPad：`~/rg-lab/dog1-navigation-20261112`，狗1本机保留原native journal和Scene原字节。
MI原命令/草稿/审批、source登记、目录provenance、最新预检、Commit/Bind及attempt、
原失败事件、安全投影`dog1-company-failure-selection.json`及Scene工具结果均保留。
私有凭据与完整Prompt不导出；未触碰狗2新动作。

## 操作端证书自然到期与恢复

准备下一轮未审批草稿时，HTTPS报client certificate expired。实际admin/viewer叶于
北京时间10:06:48自然到期，CA仍有效，NTP同步且无现有时钟跳变证据。最初只读分析
误把同样到期的旧operator-pki/server.crt当作活跃server；核对运行配置后更正：当前
Operator server位于arrival-verifier-20261111/pki/server.crt，证书仍有效，不需要续签
或Controller重启。Artifact使用另一有效server证书，也未改变。

保存数据库一致备份、原证书及完整授权后，复用原CA/原私钥续期admin/viewer七天，
原CN、principal/role/tenant不变。授权revision3→4仅追加两个新DER摘要，全部旧grant
逐值保留，没有关闭TLS、改时间或将同CN当成授权。Controller正常热读/持久化新revision，
保持PID9841；仅重启MI以刷新其缓存的客户端SSLContext。MI/Controller readiness恢复，
全部attempt逐值不变、allocations仍空、Unknown0。新叶有效至2026-11-19 10:14:50/51，
CA更早于2026-12-05到期；没有在本轮实现长期证书自动轮换。

随后最新狗1报告down、电量48%，原standing-only预检正确拒绝。没有放宽或覆盖原
单次计划。为下一轮准备独立lab gate r2（默认仍要求standing），仅在明确允许本机
必要起身的scope下可接受down，其他身份、地图、源、TTL和资源门槛不变，运动授权
字段仍false。新一轮需要重新确认现场及含必要起身的单次导航范围；不自动重发。
