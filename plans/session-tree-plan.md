# RoboGuide Remote — 显式会话树实施规划

日期: 2026-09-04
目标: 手机端从"每次 PTT 独立回合 + 扁平记录"升级为"显式会话树: 多会话、会话内多轮、上下文延续"
参考: D:\DeepRobotics\robonix-client(Web UI 的 Session 下拉/新建/重命名/清空 + history 侧栏; transport.py 的 steer + expected_turn_id 延续机制)
验收: 手机同一连接连续 3+ 句全部响应; 多会话切换互不干扰; 重启保留历史; 第二轮不再卡; versionCode 递增

---

## 背景事实(已取证)

- 当前 Thor: 每次 PTT 新建 StartVoiceSession(session_id=spp-<ts>), SESSION_DONE 后销毁, **无跨会话上下文**
- 当前 APK: `_ConversationTurn` 扁平列表, 非会话树
- 第二轮卡住: 已修复 Thor 端"一连接一会话"(0c5325cd: 结束后复位 _started/_mic_ready);
  手机端 `_startTalking` 每次 `openRecorder()` 且 `_recorder ??=` 复用 → **flutter_sound 重复 openRecorder 可能抛"already initialized"→ 第二句无音频 → 卡 recognizing**, 待子代理确认并用可观测日志实锤
- roBonix-client 参考: app.js command-bar 的 Session 选择器(#newSession 新建/#renameSession/#clearHistory) + #historyList 侧栏 + #messages; 后端 submit_task/start_voice_session 支持 steer=True + expected_turn_id=上一 turn id 表达"继续"

## 任务拆分

### P0 修复"第二轮卡住"(会话树前提)
1. 手机端: 录音器生命周期改为 open→(start→stop)*N→close; 维护 opened 状态; 第二次不再重复 openRecorder
2. 每次 PTT start/stop 都记 logcat 关键日志(openRecorder/startRecorder/stopRecorder 成败)
3. Thor 端验证: 同一 SPP 连接两次 RGAD+mic_end 应出现两个 voice session + 两次 ASR
   (0c5325cd 已实现复位, 需实测确认)
4. 判据: 手机不重连, 连说两句, Thor 出现两条 ASR final

### P1 手机端会话树
1. 模型:
   - ConversationSession {id, title(默认=首句ASR[:12]), createdAt, lastActiveAt, turns[]}
   - Turn {id, role(user/assistant/system), text, state, error, action?}
2. UI:
   - 首页顶部: 会话选择器(仿 robonix-client Session 下拉)+ 新建/重命名/清空按钮
   - 会话列表页: 卡片式, 显示 title/时间/最后一句, 点击进入, 长按或滑出删除
   - 会话详情: 消息气泡流(你=右/机器人=左)+ 状态 chip(recording/thinking/playing/done/error) + 清空
3. 持久化: shared_preferences 存 JSON(轻量), initState 恢复, 变更即存
4. PTT 在"当前会话"内追加 turn; 无会话时自动新建

### P2 上下文延续(协议层)
1. 手机 → Thor: mic_end 改带 context: RGCT {type:'mic_end', session_id, history:[{role,text}](最近5轮)}
   (保留纯 mic_end 兼容)
2. Thor → Liaison: StartVoiceSession_Request.context_json 注入 {"source":"bluetooth_spp","history":[...]}
3. Pilot 消费: 读 /home/nvidia/Desktop/robonix/system/pilot 源码确认 context 是否进 prompt;
   若消费则完成延续; 若不消费, 先退化为"history 文本拼进 task.text 前缀"由 LLM 自然理解(注明权衡)
4. 可选对齐 robonix-client: steer=true + expected_turn_id=会话内最后一条 user turn id 语义(若 Pilot 支持)

### P3 运动与上下文平衡(安全)
- 规则: 明确指令优先; 运动类 turn 标记 action 字段; 上下文只做"理解辅助", 不做"自动执行"
- UI: 运动 turn 显示执行动作摘要(如"chassis_move · 前进0.2m/s×5s"), 可回溯

### P4 构建/发布
- pubspec version: 1.0.2+3
- flutter build apk --debug → adb install -r → 桌面导出 RoboGuide-Remote-v1.0.2-debug.apk + SHA-256
- git commit(+Assisted-by trailer, AI 不署名)+ push RoboGuide-Remote
- 可选: gh Release(logged in later)

## 验收清单
- [ ] 同一连接连说 3 句, 每句都有 ASR+Pilot,TTS 回传, UI 逐句推进到 done
- [ ] 新建 2+ 会话, 各自上下文独立(第2会话说"再走一米"应指本会话语境)
- [ ] 重启 App 历史会话恢复
- [ ] 删除/重命名/清空可用
- [ ] logcat 无第二次 openRecorder 异常
- [ ] APK 1.0.2+3 安装并可分享
- [ ] 无运动指令意外重复执行(安全)

## 实施方式
单子代理串行执行(所有改动集中在 lib/main.dart + thor 脚本, 并行易冲突),
子代理上下文注入: 本规划 + 关键文件路径 + robonix-client 参考点 + 现场 SSH 取证方式。