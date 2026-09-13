package com.robonix.client.ui.i18n

import androidx.compose.runtime.Composable
import androidx.compose.runtime.staticCompositionLocalOf
import java.util.Locale

/**
 * Minimal in-app i18n: zh/en string tables with {0},{1} positional placeholders.
 *
 * UI code reads strings through the [t] / [tStatus] composables (they respect
 * [LocalAppLanguage]). ViewModels may bake strings at emission time via
 * [AppStrings.format] using the language from settings — messages already on
 * screen keep the language they were emitted in.
 */
object AppStrings {
    const val SYSTEM = "system"
    const val ZH = "zh"
    const val EN = "en"

    private val en: Map<String, String> = mapOf(
        // navigation / common actions
        "app.name" to "Robonix Client",
        "nav.chat" to "Chat",
        "nav.rtdl" to "RTDL",
        "nav.audio" to "Audio",
        "nav.settings" to "Settings",
        "conn.connecting" to "Connecting",
        "action.save" to "Save",
        "action.cancel" to "Cancel",
        "action.connect" to "Connect",
        "action.connected" to "Connected",
        "action.refresh" to "Refresh",
        "action.stop" to "Stop",
        "action.send" to "Send",
        "action.steer" to "Steer",
        "action.new" to "New",
        "action.close" to "Close",
        "action.delete" to "Delete",

        // chat
        "chat.session.default" to "Session",
        "chat.rename.title" to "Rename Session",
        "chat.rename.placeholder" to "Session name",
        "chat.history.title" to "Sessions",
        "chat.history.empty" to "No saved sessions yet.",
        "chat.untitled" to "Untitled",
        "chat.active.badge" to "Active",
        "chat.goal" to "Goal",
        "chat.goal.waiting" to "Waiting for task...",
        "chat.events" to "Events",
        "chat.empty.ready" to "Ready — type a task below",
        "chat.empty.configure" to "Configure Robot Host in Settings",
        "chat.typing" to "Robonix is working",
        "chat.wait.sec" to "{0}s",
        "chat.rtdl.preview" to "Round {0} · {1} calls · {2} results · {3} events",
        "chat.rtdl.open" to "Open RTDL →",
        "chat.configure.first" to "Configure Robot Host in Settings first.",
        "chat.task.failed" to "Task failed: {0}",
        "composer.placeholder" to "Type a message...",
        "composer.placeholder.busy" to "Steer the active task...",
        "composer.voice" to "Voice (hold to speak)",
        "composer.recording" to "Recording...",
        "composer.processing" to "Processing...",

        // rtdl
        "rtdl.tab.active" to "Active ({0})",
        "rtdl.tab.history" to "History ({0})",
        "rtdl.loading" to "Loading Executor plans...",
        "rtdl.active.empty" to "Executor reports no active RTDL plans.",
        "rtdl.live.tree" to "Live Behavior Tree",
        "rtdl.history.empty" to "No completed RTDL trees yet.",
        "rtdl.plan" to "Plan {0}",
        "rtdl.round" to "Round {0}",
        "rtdl.plan.meta" to "plan {0} · {1} ops",
        "rtdl.op" to "op {0}",
        "rtdl.node.detail" to "Node Detail",
        "rtdl.node" to "Node",
        "rtdl.node.provider" to "Provider",
        "rtdl.node.contract" to "Contract",
        "rtdl.node.status" to "Status",
        "rtdl.node.args" to "Arguments",
        "rtdl.node.result" to "Result",

        // audio
        "audio.route.title" to "Robonix Audio Route",
        "audio.route.subtitle" to "Select mic/speaker primitives",
        "audio.route.input" to "Input Primitive",
        "audio.route.output" to "Output Primitive",
        "audio.route.select.input" to "Select input primitive",
        "audio.route.select.output" to "Select output primitive",
        "audio.route.none" to "None available",
        "audio.route.status.first" to "Set Robot Host first.",
        "audio.route.status.found" to "{0} providers found. Select devices.",
        "audio.route.status.error" to "Error: {0}",
        "audio.diag.title" to "Diagnostics",
        "audio.diag.subtitle" to "Test audio devices",
        "audio.diag.grant" to "Grant Microphone Permission",
        "audio.diag.mic" to "Test Mic",
        "audio.diag.spk" to "Test Speaker",
        "audio.diag.capturing" to "Capturing 1s...",
        "audio.diag.mic.ok" to "OK: {0} bytes, RMS {1}",
        "audio.diag.failed" to "Failed: {0}",
        "audio.diag.tone" to "Playing tone...",
        "audio.diag.tone.ok" to "Tone played",
        "audio.vu.title" to "Input Level",
        "audio.vu.subtitle" to "Real-time VU meter",
        "audio.vu.stop" to "Stop Recording",
        "audio.vu.hint" to "Start recording to see levels.",
        "audio.vu.start" to "Start Recording",
        "audio.vp.title" to "Voiceprint",
        "audio.vp.subtitle" to "Enroll your voice",
        "audio.vp.user" to "User ID",
        "audio.vp.enroll" to "Enroll Voice",
        "audio.log.title" to "Audio Log",
        "audio.log.subtitle" to "Recent events",
        "audio.log.empty" to "No log entries yet.",

        // settings
        "settings.connection" to "Connection",
        "settings.connection.desc" to "Enter robot connection details.",
        "settings.host" to "Robot Host",
        "settings.port" to "Atlas Port",
        "settings.liaison" to "Liaison Endpoint",
        "settings.liaison.placeholder" to "Auto-discover from Atlas",
        "settings.userid" to "User ID",
        "settings.system" to "System Info",
        "settings.metric.providers" to "Providers",
        "settings.metric.active" to "Active",
        "settings.metric.errors" to "Errors",
        "settings.contracts" to "Contracts",
        "settings.contract.ok" to "ok",
        "settings.contract.missing" to "missing",
        "settings.providers" to "Providers ({0})",
        "settings.providers.more" to "... +{0} more",
        "settings.system.hint" to "Configure connection and click Connect",
        "settings.about.desc" to "Native client for Robonix robot control.",
        "settings.prefs" to "Preferences",
        "settings.prefs.desc" to "Language and voice input.",
        "settings.language" to "Language",
        "settings.language.system" to "Follow system",
        "settings.language.zh" to "中文",
        "settings.language.en" to "English",
        "settings.record" to "Voice clip length",
        "settings.record.desc" to "Hold-to-talk auto-stops after this length",
        "settings.record.value" to "{0}s",

        // snackbar / misc messages
        "msg.settings.saved" to "Settings saved",
        "common.unknown.error" to "unknown error",
        "chat.stop.failed" to "Stop failed: {0}",
        "chat.voice.failed" to "Voice failed: {0}",
        "audio.route.status.initial" to "Loading audio route...",

        // plan announce / tts feedback / session management
        "chat.plan.calls" to "Calling {0}",
        "chat.plan.more" to "+{0} more",
        "chat.plan.round" to "RTDL plan round {0}",
        "chat.tts.started" to "TTS playback started",
        "chat.tts.done" to "TTS playback done",
        "chat.tts.speaking" to "Speaking...",
        "chat.history.clear" to "Clear All",
        "chat.history.clear.title" to "Clear all sessions?",
        "chat.history.clear.text" to "This deletes every session saved on this device and resets the current chat.",
        "action.confirm" to "Confirm",

        // audio route devices / apply / voiceprint enroll
        "audio.route.device.input" to "Microphone device",
        "audio.route.device.output" to "Speaker device",
        "audio.route.device.none" to "Select a provider first / no devices",
        "audio.route.device.loading" to "Loading devices...",
        "audio.route.apply" to "Apply",
        "audio.route.applying" to "Applying selected devices...",
        "audio.route.applied" to "Route applied to {0} device(s).",
        "audio.route.apply.failed" to "Route apply failed: {0}",
        "audio.vp.recording" to "Recording 6s — speak now",
        "audio.vp.enrolled" to "Voiceprint enrolled: {0}",
        "audio.vp.failed" to "Enroll failed: {0}",

        // hands-free mode
        "audio.hf.title" to "Hands-free",
        "audio.hf.subtitle" to "Wake-word triggered voice sessions",
        "audio.hf.on" to "Listening for wake word",
        "audio.hf.off" to "Off",
        "audio.hf.keyword" to "Wake word: {0}",
        "audio.hf.last.transcript" to "Last heard: {0}",
        "audio.hf.voicebusy" to "Unavailable during a voice session",
        "audio.hf.error" to "{0}",
        "status.LISTENING" to "listening",
        "status.TRIGGERED" to "wake word detected",
        "status.ACKNOWLEDGING" to "acknowledging",
        "status.IN_VOICE" to "in voice session",
        "status.SUSPENDED" to "suspended",

        // vitals health center
        "nav.vitals" to "Vitals",
        "vitals.summary" to "Overall health",
        "vitals.modules" to "{0} modules",
        "vitals.providers" to "{0} providers",
        "vitals.software" to "Service health",
        "vitals.view.modules" to "Modules",
        "vitals.view.providers" to "Providers",
        "vitals.caps" to "{0} caps",
        "vitals.empty" to "No health data yet",
        "vitals.loading" to "Loading health data...",
        "vitals.error" to "{0}",
        "vitals.detail.title" to "Module detail",
        "vitals.key" to "Module key",
        "vitals.id" to "Module id",
        "vitals.provider" to "Provider",
        "vitals.state" to "State",
        "vitals.health" to "Health",
        "vitals.reason" to "Reason",
        "vitals.source" to "Source",
        "vitals.updated" to "Updated",
        "vitals.detail" to "Detail",
        "vitals.topology" to "Robot components",
        "vitals.signals" to "Signals",
        "vitals.components" to "{0} components",
        "vitals.charging" to "Charging",

        // timeline
        "tl.task" to "task: {0}",
        "tl.steer" to "steer: {0}",
        "tl.abort" to "abort requested",
        "tl.plan.round" to "live round {0}: {1} call(s)",
        "tl.round.result" to "round {0} result",
        "tl.node" to "node {0}",
        "tl.tts.started" to "TTS started",
        "tl.tts.done" to "TTS done",
        "tl.recording.started" to "Recording started",
        "tl.recording.done" to "Recording done",
        "tl.finishing" to "finishing recording, submitting recognized text",
        "tl.session.started" to "Session started",
        "tl.session.done" to "Session done",
        "tl.session.requested" to "voice session requested",
        "tl.steer.requested" to "voice steer requested",
        "tl.partial" to "partial: {0}",
        "tl.error.task" to "task error: {0}",
        "tl.error.abort" to "abort error: {0}",
        "tl.error.voice" to "voice error: {0}",
        "msg.settings.save.failed" to "Save failed: {0}",
        "msg.host.first" to "Set Robot Host first",

        // statuses (server-provided values, translated for display)
        "status.READY" to "Ready",
        "status.DEGRADED" to "Degraded",
        "status.IDLE" to "Idle",
        "status.OFFLINE" to "Offline",
        "status.ONLINE" to "Online",
        "status.SUCCEEDED" to "Succeeded",
        "status.SUCCESS" to "Success",
        "status.RUNNING" to "Running",
        "status.FAILED" to "Failed",
        "status.CANCELED" to "Canceled",
        "status.CANCELING" to "Canceling",
        "status.TIMEOUT" to "Timeout",
        "status.PENDING" to "Pending",
        "status.IN_PROGRESS" to "In progress",
        "status.ERROR" to "Error",
        "status.ACTIVE" to "Active",
        "status.INACTIVE" to "Inactive",
        "status.TERMINATED" to "Terminated",
        "status.CONNECTING" to "Connecting",
        "status.OK" to "OK",
        "status.UNKNOWN" to "Unknown",
        "status.WARN" to "Warn",
        "status.STALE" to "Stale",
    )

    private val zh: Map<String, String> = mapOf(
        // navigation / common actions
        "app.name" to "Robonix 客户端",
        "nav.chat" to "对话",
        "nav.rtdl" to "RTDL",
        "nav.audio" to "音频",
        "nav.settings" to "设置",
        "conn.connecting" to "连接中",
        "action.save" to "保存",
        "action.cancel" to "取消",
        "action.connect" to "连接",
        "action.connected" to "已连接",
        "action.refresh" to "刷新",
        "action.stop" to "停止",
        "action.send" to "发送",
        "action.steer" to "干预",
        "action.new" to "新建",
        "action.close" to "关闭",
        "action.delete" to "删除",

        // chat
        "chat.session.default" to "会话",
        "chat.rename.title" to "重命名会话",
        "chat.rename.placeholder" to "会话名称",
        "chat.history.title" to "历史会话",
        "chat.history.empty" to "暂无保存的会话",
        "chat.untitled" to "未命名",
        "chat.active.badge" to "当前",
        "chat.goal" to "目标",
        "chat.goal.waiting" to "等待任务…",
        "chat.events" to "事件",
        "chat.empty.ready" to "已就绪，在下方输入任务",
        "chat.empty.configure" to "请先在设置中配置机器人地址",
        "chat.typing" to "Robonix 正在处理",
        "chat.wait.sec" to "{0} 秒",
        "chat.rtdl.preview" to "第 {0} 轮 · {1} 次调用 · {2} 条结果 · {3} 条事件",
        "chat.rtdl.open" to "打开 RTDL →",
        "chat.configure.first" to "请先在设置中配置机器人地址。",
        "chat.task.failed" to "任务失败：{0}",
        "composer.placeholder" to "输入消息…",
        "composer.placeholder.busy" to "干预当前任务…",
        "composer.voice" to "语音（按住说话）",
        "composer.recording" to "录音中…",
        "composer.processing" to "识别中…",

        // rtdl
        "rtdl.tab.active" to "进行中（{0}）",
        "rtdl.tab.history" to "历史（{0}）",
        "rtdl.loading" to "正在加载执行器计划…",
        "rtdl.active.empty" to "执行器暂无进行中的 RTDL 计划",
        "rtdl.live.tree" to "实时行为树",
        "rtdl.history.empty" to "暂无已完成的 RTDL 树",
        "rtdl.plan" to "计划 {0}",
        "rtdl.round" to "第 {0} 轮",
        "rtdl.plan.meta" to "计划 {0} · {1} 个操作",
        "rtdl.op" to "操作 {0}",
        "rtdl.node.detail" to "节点详情",
        "rtdl.node" to "节点",
        "rtdl.node.provider" to "提供者",
        "rtdl.node.contract" to "契约",
        "rtdl.node.status" to "状态",
        "rtdl.node.args" to "参数",
        "rtdl.node.result" to "结果",

        // audio
        "audio.route.title" to "音频路由",
        "audio.route.subtitle" to "选择麦克风/扬声器组件",
        "audio.route.input" to "输入组件",
        "audio.route.output" to "输出组件",
        "audio.route.select.input" to "选择输入组件",
        "audio.route.select.output" to "选择输出组件",
        "audio.route.none" to "暂无可用选项",
        "audio.route.status.first" to "请先设置机器人地址。",
        "audio.route.status.found" to "找到 {0} 个组件，请选择设备",
        "audio.route.status.error" to "错误：{0}",
        "audio.diag.title" to "诊断",
        "audio.diag.subtitle" to "测试音频设备",
        "audio.diag.grant" to "授权麦克风权限",
        "audio.diag.mic" to "测试麦克风",
        "audio.diag.spk" to "测试扬声器",
        "audio.diag.capturing" to "正在采集 1 秒…",
        "audio.diag.mic.ok" to "正常：{0} 字节，RMS {1}",
        "audio.diag.failed" to "失败：{0}",
        "audio.diag.tone" to "正在播放提示音…",
        "audio.diag.tone.ok" to "提示音已播放",
        "audio.vu.title" to "输入电平",
        "audio.vu.subtitle" to "实时音量指示",
        "audio.vu.stop" to "停止录音",
        "audio.vu.hint" to "开始录音以查看电平",
        "audio.vu.start" to "开始录音",
        "audio.vp.title" to "声纹",
        "audio.vp.subtitle" to "注册你的声音",
        "audio.vp.user" to "用户 ID",
        "audio.vp.enroll" to "注册声纹",
        "audio.log.title" to "音频日志",
        "audio.log.subtitle" to "近期事件",
        "audio.log.empty" to "暂无日志",

        // settings
        "settings.connection" to "连接",
        "settings.connection.desc" to "填写机器人连接信息",
        "settings.host" to "机器人地址",
        "settings.port" to "Atlas 端口",
        "settings.liaison" to "Liaison 地址",
        "settings.liaison.placeholder" to "由 Atlas 自动发现",
        "settings.userid" to "用户 ID",
        "settings.system" to "系统信息",
        "settings.metric.providers" to "组件",
        "settings.metric.active" to "活跃",
        "settings.metric.errors" to "错误",
        "settings.contracts" to "契约",
        "settings.contract.ok" to "正常",
        "settings.contract.missing" to "缺失",
        "settings.providers" to "组件（{0}）",
        "settings.providers.more" to "…还有 {0} 个",
        "settings.system.hint" to "配置连接信息后点击连接",
        "settings.about.desc" to "Robonix 机器人原生客户端",
        "settings.prefs" to "偏好设置",
        "settings.prefs.desc" to "语言与语音输入",
        "settings.language" to "语言",
        "settings.language.system" to "跟随系统",
        "settings.language.zh" to "中文",
        "settings.language.en" to "English",
        "settings.record" to "语音片段时长",
        "settings.record.desc" to "按住说话达到该时长后自动结束",
        "settings.record.value" to "{0} 秒",

        // snackbar / misc messages
        "msg.settings.saved" to "设置已保存",
        "common.unknown.error" to "未知错误",
        "chat.stop.failed" to "停止失败：{0}",
        "chat.voice.failed" to "语音失败：{0}",
        "audio.route.status.initial" to "正在加载音频路由…",

        // plan announce / tts feedback / session management
        "chat.plan.calls" to "正在调用 {0}",
        "chat.plan.more" to "等 {0} 项",
        "chat.plan.round" to "RTDL 计划第 {0} 轮",
        "chat.tts.started" to "TTS 开始播报",
        "chat.tts.done" to "TTS 播报完成",
        "chat.tts.speaking" to "正在播报…",
        "chat.history.clear" to "清空全部",
        "chat.history.clear.title" to "清空全部会话？",
        "chat.history.clear.text" to "将删除本机保存的全部会话，并重置当前对话。",
        "action.confirm" to "确认",

        // audio route devices / apply / voiceprint enroll
        "audio.route.device.input" to "麦克风设备",
        "audio.route.device.output" to "扬声器设备",
        "audio.route.device.none" to "请先选择提供方 / 无可用设备",
        "audio.route.device.loading" to "正在加载设备…",
        "audio.route.apply" to "应用",
        "audio.route.applying" to "正在应用所选设备…",
        "audio.route.applied" to "已应用到 {0} 个设备。",
        "audio.route.apply.failed" to "应用失败：{0}",
        "audio.vp.recording" to "正在录音 6 秒，请说话",
        "audio.vp.enrolled" to "声纹注册成功：{0}",
        "audio.vp.failed" to "注册失败：{0}",

        // hands-free mode
        "audio.hf.title" to "免提模式",
        "audio.hf.subtitle" to "唤醒词触发的语音会话",
        "audio.hf.on" to "正在等待唤醒词",
        "audio.hf.off" to "未开启",
        "audio.hf.keyword" to "唤醒词：{0}",
        "audio.hf.last.transcript" to "最后听到：{0}",
        "audio.hf.voicebusy" to "语音会话进行中暂不可用",
        "audio.hf.error" to "{0}",
        "status.LISTENING" to "监听中",
        "status.TRIGGERED" to "检测到唤醒词",
        "status.ACKNOWLEDGING" to "应答中",
        "status.IN_VOICE" to "语音会话中",
        "status.SUSPENDED" to "已暂停",

        // vitals health center
        "nav.vitals" to "健康",
        "vitals.summary" to "总体健康度",
        "vitals.modules" to "{0} 个模块",
        "vitals.providers" to "{0} 个提供者",
        "vitals.software" to "服务健康",
        "vitals.view.modules" to "模块",
        "vitals.view.providers" to "提供者",
        "vitals.caps" to "{0} 项能力",
        "vitals.empty" to "暂无健康数据",
        "vitals.loading" to "正在加载健康数据…",
        "vitals.error" to "{0}",
        "vitals.detail.title" to "模块详情",
        "vitals.key" to "模块标识",
        "vitals.id" to "模块 ID",
        "vitals.provider" to "提供方",
        "vitals.state" to "状态",
        "vitals.health" to "健康度",
        "vitals.reason" to "原因",
        "vitals.source" to "来源",
        "vitals.updated" to "更新时间",
        "vitals.detail" to "详情",
        "vitals.topology" to "机器人部件",
        "vitals.signals" to "信号",
        "vitals.components" to "{0} 个部件",
        "vitals.charging" to "充电中",

        // timeline
        "tl.task" to "任务：{0}",
        "tl.steer" to "干预：{0}",
        "tl.abort" to "已请求中止",
        "tl.plan.round" to "实时第 {0} 轮：{1} 次调用",
        "tl.round.result" to "第 {0} 轮结果",
        "tl.node" to "节点 {0}",
        "tl.tts.started" to "TTS 开始",
        "tl.tts.done" to "TTS 结束",
        "tl.recording.started" to "录音开始",
        "tl.recording.done" to "录音结束",
        "tl.finishing" to "正在结束录音，提交已识别内容",
        "tl.session.started" to "会话已开始",
        "tl.session.done" to "会话已结束",
        "tl.session.requested" to "语音会话已请求",
        "tl.steer.requested" to "语音干预已请求",
        "tl.partial" to "部分识别：{0}",
        "tl.error.task" to "任务错误：{0}",
        "tl.error.abort" to "中止错误：{0}",
        "tl.error.voice" to "语音错误：{0}",
        "msg.settings.save.failed" to "保存失败：{0}",
        "msg.host.first" to "请先设置机器人地址",

        // statuses (server-provided values, translated for display)
        "status.READY" to "就绪",
        "status.DEGRADED" to "降级",
        "status.IDLE" to "空闲",
        "status.OFFLINE" to "未连接",
        "status.ONLINE" to "已连接",
        "status.SUCCEEDED" to "成功",
        "status.SUCCESS" to "成功",
        "status.RUNNING" to "运行中",
        "status.FAILED" to "失败",
        "status.CANCELED" to "已取消",
        "status.CANCELING" to "取消中",
        "status.TIMEOUT" to "超时",
        "status.PENDING" to "等待中",
        "status.IN_PROGRESS" to "进行中",
        "status.ERROR" to "错误",
        "status.ACTIVE" to "活跃",
        "status.INACTIVE" to "停用",
        "status.TERMINATED" to "已终止",
        "status.CONNECTING" to "连接中",
        "status.OK" to "正常",
        "status.UNKNOWN" to "未知",
        "status.WARN" to "警告",
        "status.STALE" to "过期",
    )

    private val dictionaries = mapOf(ZH to zh, EN to en)

    /** Resolve a settings value ("system"/"zh"/"en") into a concrete dict language. */
    fun resolveLanguage(setting: String): String = when (setting) {
        ZH -> ZH
        EN -> EN
        else -> if (Locale.getDefault().language.startsWith("zh")) ZH else EN
    }

    private val argRegex = Regex("""\{(\d+)\}""")

    fun format(lang: String, key: String, vararg args: Any?): String {
        val dict = dictionaries[resolveLanguage(lang)] ?: en
        var text = dict[key] ?: en[key] ?: key
        if (args.isNotEmpty()) {
            text = argRegex.replace(text) { m ->
                args.getOrNull(m.groupValues[1].toInt())?.toString() ?: m.value
            }
        }
        return text
    }

    /** Translate a server status word (e.g. "RUNNING", "ready") for display. */
    fun formatStatus(lang: String, status: String): String {
        val raw = status.trim()
        if (raw.isEmpty()) return raw
        val key = "status.${raw.uppercase()}"
        val dict = dictionaries[resolveLanguage(lang)] ?: en
        return dict[key] ?: en[key] ?: raw
    }
}

val LocalAppLanguage = staticCompositionLocalOf { AppStrings.EN }

@Composable
fun t(key: String, vararg args: Any?): String =
    AppStrings.format(LocalAppLanguage.current, key, *args)

@Composable
fun tStatus(status: String): String =
    AppStrings.formatStatus(LocalAppLanguage.current, status)
