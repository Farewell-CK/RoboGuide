package com.seaway.guideassistant.fragment

import android.Manifest
import android.location.Location
import android.net.Uri
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.util.Log
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.doOnLayout
import androidx.core.widget.doOnTextChanged
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.elabrador.mobilenavigation.GuidanceLevel
import com.elabrador.mobilenavigation.LocalPlanSnapshot
import com.elabrador.mobilenavigation.OutdoorNavController
import com.elabrador.mobilenavigation.PlaceSuggestion
import com.elabrador.mobilenavigation.VisionHintSettings
import com.seaway.guideassistant.R
import com.seaway.guideassistant.base.BaseBindFragment
import com.seaway.guideassistant.base.Constant
import com.seaway.guideassistant.ble.GlassesManager
import com.seaway.guideassistant.databinding.FragmentNavigateBinding
import com.seaway.guideassistant.navigation.NavigationPlanManager
import com.seaway.guideassistant.robot.RobotConnectionManager
import com.seaway.guideassistant.robot.RobotConversationLog
import com.seaway.guideassistant.utils.announceA11y
import com.seaway.guideassistant.voice.VoiceErrorReason
import com.seaway.guideassistant.voice.VoiceInputController
import com.seaway.guideassistant.ws.DeviceType
import com.seaway.guideassistant.ws.DeviceWatchClient
import com.seaway.smallutils.ToastUtil
import com.seaway.smallutils.TtsUtils
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import org.koin.android.ext.android.get
import org.koin.core.qualifier.named
import org.videolan.libvlc.LibVLC
import org.videolan.libvlc.Media
import org.videolan.libvlc.MediaPlayer as VlcMediaPlayer

class NavigateFragment : BaseBindFragment<FragmentNavigateBinding>() {

    companion object {
        private const val TAG_GLASSES_FEED = "NavGlassesFeed"
    }

    private var outdoorController: OutdoorNavController? = null
    private var voiceController: VoiceInputController? = null
    private var lastDispatchedPhaseIndex = -1
    private var lastDispatchedPlanId = -1
    private var lastAnnouncedCompleted = false
    private var lastSpokenGuidance: String? = null

    /** true：已点击"开始导航"进入室外导航中（对应 groupOutdoorActive 可见），眼镜画面只在此时渲染。 */
    private val outdoorNavigationActive = MutableStateFlow(false)

    /** true：当前展示室内阶段 UI（groupIndoorMode 可见）。室外导航不依赖 NavigationPlanManager 的计划
     * 状态——用户可以不经过 Agent 下发计划、直接在本页手动"开始导航"，这种场景下 PlanState 一直是
     * Idle，若拿它做直播开关的前置条件，眼镜直播永远不会被打开。室内阶段本身就是计划驱动的，这里仍然
     * 用 renderPlanState 已经算出来的 showIndoor 同步即可。 */
    private val indoorPhaseActive = MutableStateFlow(false)
    private var libVLC: LibVLC? = null
    private var glassesPlayer: VlcMediaPlayer? = null
    private var glassesStreamOn = false
    private var wantGlassesRender = false
    private var glassesPlaying = false
    private val glassesRetryHandler = Handler(Looper.getMainLooper())
    private var glassesRetryCount = 0
    private val maxGlassesRetries = 5

    private data class GlassesFeedDecision(val streamOn: Boolean, val render: Boolean)

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { grants ->
            if (grants[Manifest.permission.CAMERA] == true) {
                outdoorController?.onCameraPermissionGranted()
            }
            if (grants[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
                grants[Manifest.permission.ACCESS_COARSE_LOCATION] == true
            ) {
                outdoorController?.onLocationPermissionGranted()
            }
            if (grants[Manifest.permission.NEARBY_WIFI_DEVICES] == true && glassesStreamOn) {
                GlassesManager.startLiveStream()
            }
        }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        outdoorController = OutdoorNavController(requireContext(), Constant.AMAP_KEY, OutdoorListener())
        val visionSettings = outdoorController?.visionHintSettings()
        bind.switchVisionHints.isChecked = visionSettings?.enabled ?: true
        bind.switchVisionHints.setOnCheckedChangeListener { _, enabled ->
            val current = outdoorController?.visionHintSettings() ?: return@setOnCheckedChangeListener
            outdoorController?.updateVisionHintSettings(current.copy(enabled = enabled))
        }
        bind.btnVisionSettings.setOnClickListener { showVisionHintSettingsDialog() }
        bind.btnPlanRoute.setOnClickListener { outdoorController?.planRoute(bind.etEnd.text.toString()) }
        bind.btnStartStop.setOnClickListener { outdoorController?.endNavigation() }
        bind.btnCalibrateHeading.setOnClickListener { outdoorController?.calibrateHeading() }
        bind.btnEndIndoor.setOnClickListener { finishIndoorPhase() }
        bind.etEnd.doOnTextChanged { text, _, _, _ -> outdoorController?.searchDestination(text?.toString().orEmpty()) }
        bind.btnIndoorSend.setOnClickListener { sendIndoorManualInput() }
        voiceController = VoiceInputController(requireActivity(), get<OkHttpClient>(named("webSocket")), object : VoiceInputController.Callbacks {
            override fun onListeningStarted() = updateIndoorVoiceButton(recording = true)
            override fun onTranscript(text: String) = handleIndoorTranscript(text)
            override fun onError(reason: VoiceErrorReason) = handleIndoorVoiceError(reason)
        })
        bind.btnIndoorVoice.setOnClickListener { voiceController?.toggle() }
        observePlan()
        observeRobotStatus()
        observeRobotLog()
        observeGlassesFeed()
    }

    override fun onResume() {
        super.onResume()
        outdoorController?.onResume()
        if (wantGlassesRender && !glassesPlaying) {
            readyGlassesUrl()?.let(::startGlassesPlayback)
        }
    }

    override fun onPause() {
        outdoorController?.onPause()
        pauseGlassesPlayback()
        super.onPause()
    }

    override fun onDestroyView() {
        outdoorController?.onDestroy()
        outdoorController = null
        voiceController?.release()
        voiceController = null
        glassesRetryHandler.removeCallbacksAndMessages(null)
        glassesPlayer?.stop()
        glassesPlayer?.detachViews()
        glassesPlayer?.release()
        glassesPlayer = null
        libVLC?.release()
        libVLC = null
        // 新视图里的 VLCVideoLayout 默认是 GONE，必须让下次决策重新走一遍 showGlassesPreview
        wantGlassesRender = false
        glassesPlaying = false
        glassesRetryCount = 0
        if (glassesStreamOn) {
            glassesStreamOn = false
            GlassesManager.stopLiveStream()
        }
        super.onDestroyView()
    }

    /** 订阅 NavigationPlanManager 广播的多阶段计划状态，按当前阶段切换室内/室外模式内容 */
    private fun observePlan() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                NavigationPlanManager.state.collect { renderPlanState(it) }
            }
        }
    }

    private fun observeRobotStatus() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                RobotConnectionManager.connectionState.collect { state ->
                    bind.tvRobotStatus.text = when (state) {
                        is RobotConnectionManager.ConnectionState.Idle -> getString(R.string.robot_status_disconnected)
                        is RobotConnectionManager.ConnectionState.Connecting -> getString(R.string.robot_status_connecting)
                        is RobotConnectionManager.ConnectionState.Connected -> getString(R.string.robot_status_connected)
                        is RobotConnectionManager.ConnectionState.Failed -> getString(R.string.robot_status_failed_fmt, state.reason)
                    }
                }
            }
        }
    }

    private fun observeRobotLog() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                RobotConversationLog.entries.collect { entries -> renderRobotLog(entries) }
            }
        }
    }

    /** 持续判断眼镜画面要不要开/要不要渲染：室内外任一阶段进行中 + 眼镜已连接 + 电量 > 30% 就开直播
     * （[GlassesManager.startLiveStream]），画面渲染只在室外且已点击"开始导航"时才启用。 */
    private fun observeGlassesFeed() {
        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch {
                    combine(
                        GlassesManager.connectionState,
                        GlassesManager.batteryState,
                        outdoorNavigationActive,
                        indoorPhaseActive
                    ) { conn, battery, outdoorActive, indoorActive ->
                        val eligible = conn is GlassesManager.ConnectionState.Connected && (battery?.level ?: 0) > 30
                        val navInProgress = outdoorActive || indoorActive
                        Log.d(TAG_GLASSES_FEED, "状态: conn=$conn battery=$battery outdoorActive=$outdoorActive indoorActive=$indoorActive eligible=$eligible")
                        GlassesFeedDecision(streamOn = eligible && navInProgress, render = eligible && outdoorActive)
                    }.distinctUntilChanged().collect(::applyGlassesFeedDecision)
                }
                launch {
                    combine(GlassesManager.liveUrl, GlassesManager.liveWifiConnected) { url, wifi ->
                        Log.d(TAG_GLASSES_FEED, "liveUrl=$url wifiConnected=$wifi wantGlassesRender=$wantGlassesRender glassesPlaying=$glassesPlaying")
                        url.takeIf { wifi }
                    }.collect { url ->
                        if (url != null && wantGlassesRender && !glassesPlaying) startGlassesPlayback(url)
                    }
                }
            }
        }
    }

    private fun applyGlassesFeedDecision(decision: GlassesFeedDecision) {
        Log.d(TAG_GLASSES_FEED, "决策: streamOn=${decision.streamOn} render=${decision.render} currentStreamOn=$glassesStreamOn currentRender=$wantGlassesRender liveUrl=${GlassesManager.liveUrl.value}")
        if (decision.streamOn != glassesStreamOn) {
            glassesStreamOn = decision.streamOn
            if (decision.streamOn) startGlassesLiveStreamWithPermission() else GlassesManager.stopLiveStream()
        }
        if (decision.render != wantGlassesRender) {
            wantGlassesRender = decision.render
            // 可见性切换放在接流之前：VLCVideoLayout 在 View.GONE 时不会创建原生渲染 Surface，
            // attachViews/play 必须在它已经可见、能正常走 measure/layout 之后才有画面，不能等
            // 播放成功了才切可见——那时 Surface 可能一直没建好，画面就一直黑/卡在旧画面上。
            showGlassesPreview(decision.render)
        }
        if (!decision.render) {
            // 室内阶段也会走到这支分支：只保持 streamOn、不建播放器。后续目标检测模块会自行订阅
            // GlassesManager.liveUrl 起一条解码管线消费帧数据，这里不重复实现。
            stopGlassesPlayback()
        } else if (!glassesPlaying) {
            readyGlassesUrl()?.let(::startGlassesPlayback)
        }
    }

    /** Android 13+ 上眼镜 SDK 连直播 Wi-Fi（Wi-Fi Direct / WifiNetworkSpecifier）需要运行时授予
     * NEARBY_WIFI_DEVICES，仅在清单里声明不够，未授权时连接会直接失败、拿不到直播地址。 */
    private fun startGlassesLiveStreamWithPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(requireContext(), Manifest.permission.NEARBY_WIFI_DEVICES)
            != PackageManager.PERMISSION_GRANTED
        ) {
            permissionLauncher.launch(arrayOf(Manifest.permission.NEARBY_WIFI_DEVICES))
            return
        }
        GlassesManager.startLiveStream()
    }

    private fun showGlassesPreview(showGlasses: Boolean) {
        bind.containerGlassesPreview.visibility = if (showGlasses) View.VISIBLE else View.GONE
        bind.ivColorPreview.visibility = if (showGlasses) View.GONE else View.VISIBLE
        bind.tvColorPreviewLabel.text = getString(
            if (showGlasses) R.string.nav_color_preview_label_glasses else R.string.nav_color_preview_label_camera
        )
    }

    private fun readyGlassesUrl(): String? =
        GlassesManager.liveUrl.value.takeIf { GlassesManager.liveWifiConnected.value }

    private fun startGlassesPlayback(url: String) {
        val ctx = context ?: return
        val vlc = libVLC ?: LibVLC(ctx, arrayListOf("--rtsp-tcp")).also { libVLC = it }
        val player = glassesPlayer ?: VlcMediaPlayer(vlc).also { created ->
            glassesPlayer = created
            created.setEventListener { event ->
                when (event.type) {
                    VlcMediaPlayer.Event.EncounteredError -> {
                        Log.e(TAG_GLASSES_FEED, "眼镜直播播放失败: $url")
                        glassesPlaying = false
                        val retryUrl = GlassesManager.liveUrl.value
                        if (wantGlassesRender && retryUrl != null && glassesRetryCount < maxGlassesRetries) {
                            glassesRetryCount++
                            glassesRetryHandler.postDelayed({ startGlassesPlayback(retryUrl) }, 1000L * glassesRetryCount)
                        }
                    }
                    VlcMediaPlayer.Event.Playing -> {
                        Log.d(TAG_GLASSES_FEED, "眼镜直播开始播放: $url")
                        glassesRetryCount = 0
                    }
                }
            }
        }
        try {
            if (player.vlcVout.areViewsAttached()) {
                player.detachViews()
            }
            player.attachViews(bind.vlcGlassesPreview, null, false, false)
            // SDK 回调的地址带结尾斜杠，眼镜端 RTSP 服务对挂载点路径是精确匹配，带斜杠请求会 404
            val media = Media(vlc, Uri.parse(url.trimEnd('/')))
            player.media = media
            media.release()
            player.play()
            glassesPlaying = true
            Log.d(TAG_GLASSES_FEED, "眼镜直播已发起播放: $url")
        } catch (error: Exception) {
            Log.e(TAG_GLASSES_FEED, "眼镜直播播放异常: $url", error)
            glassesPlaying = false
        }
    }

    private fun stopGlassesPlayback() {
        glassesRetryHandler.removeCallbacksAndMessages(null)
        glassesRetryCount = 0
        glassesPlayer?.let { p ->
            try {
                p.stop()
            } catch (e: Exception) {
                Log.e(TAG_GLASSES_FEED, "stop 异常", e)
            }
            if (p.vlcVout.areViewsAttached()) {
                p.detachViews()
            }
        }
        glassesPlaying = false
    }

    /** 屏幕短暂熄灭/切后台时只停播放器、不关直播流（Wi-Fi 直播留着，同
     * OutdoorNavController.onPause 对相机流的处理方式），避免来回开关直播。 */
    private fun pauseGlassesPlayback() {
        glassesRetryHandler.removeCallbacksAndMessages(null)
        if (glassesPlaying) {
            glassesPlayer?.stop()
            glassesPlayer?.detachViews()
            glassesPlaying = false
        }
    }

    private fun renderRobotLog(entries: List<RobotConversationLog.Entry>) {
        bind.containerRobotLog.removeAllViews()
        if (entries.isEmpty()) {
            val empty = TextView(context).apply {
                text = getString(R.string.robot_log_empty)
                setTextColor(ContextCompat.getColor(requireContext(), R.color.color_text_muted))
                textSize = 13f
            }
            bind.containerRobotLog.addView(empty)
            return
        }
        entries.forEach { entry ->
            val row = TextView(context).apply {
                text = getString(R.string.robot_log_entry_fmt, entry.timestamp, entry.instruction, entry.resultSummary)
                setTextColor(ContextCompat.getColor(requireContext(), R.color.color_text))
                textSize = 13f
                setPadding(0, resources.getDimensionPixelSize(R.dimen.dp_6), 0, 0)
            }
            bind.containerRobotLog.addView(row)
            bind.sv.doOnLayout {
                bind.sv.fullScroll(View.FOCUS_DOWN)
            }
        }
    }

    private fun renderPlanState(state: NavigationPlanManager.PlanState) {
        val inProgress = state as? NavigationPlanManager.PlanState.InProgress
        val phase = inProgress?.let { it.plan.phases[it.phaseIndex] }

        if (inProgress != null && phase != null) {
            bind.tvPhaseStatus.visibility = View.VISIBLE
            bind.tvPhaseStatus.text = buildPhaseStatusText(inProgress)
        } else {
            bind.tvPhaseStatus.visibility = View.GONE
        }

        val showIndoor = phase?.mode == "indoor"
        bind.groupIndoorMode.visibility = if (showIndoor) View.VISIBLE else View.GONE
        bind.groupOutdoorMode.visibility = if (showIndoor) View.GONE else View.VISIBLE
        indoorPhaseActive.value = showIndoor

        if (inProgress != null &&
            (inProgress.planId != lastDispatchedPlanId || inProgress.phaseIndex != lastDispatchedPhaseIndex)
        ) {
            lastDispatchedPlanId = inProgress.planId
            lastDispatchedPhaseIndex = inProgress.phaseIndex
            if (showIndoor) {
                dispatchIndoorInstruction(phase!!.description)
            } else {
                enterOutdoorPhase(inProgress.plan.destination)
            }
        }

        if (state is NavigationPlanManager.PlanState.Completed) {
            outdoorNavigationActive.value = false
            if (!lastAnnouncedCompleted) {
                lastAnnouncedCompleted = true
                bind.root.announceA11y(getString(R.string.nav_all_phases_done))
            }
            showOutdoorSelectGroup()
        } else {
            lastAnnouncedCompleted = false
        }

        if (state is NavigationPlanManager.PlanState.Idle) {
            lastDispatchedPhaseIndex = -1
            lastDispatchedPlanId = -1
        }
    }

    /** 拼出顶部状态区文案：AgentFragment 下发的完整指令 + 当前任务 + 下一步任务（若还有） */
    private fun buildPhaseStatusText(inProgress: NavigationPlanManager.PlanState.InProgress): String {
        val total = inProgress.plan.phases.size
        val currentPhase = inProgress.plan.phases[inProgress.phaseIndex]
        val currentLine = getString(
            R.string.nav_phase_status_fmt,
            inProgress.phaseIndex + 1, total, currentPhase.description
        )
        val lines = mutableListOf(
            getString(R.string.nav_instruction_dispatch_fmt, inProgress.plan.rawInstruction),
            getString(R.string.nav_current_task_fmt, currentLine)
        )
        inProgress.plan.phases.getOrNull(inProgress.phaseIndex + 1)?.let { next ->
            val nextLine = getString(R.string.nav_phase_status_fmt, inProgress.phaseIndex + 2, total, next.description)
            lines += getString(R.string.nav_next_task_fmt, nextLine)
        }
        return lines.joinToString("\n")
    }

    /** 把当前室内阶段的指令下发给机器狗：清空旧记录后重新下发。是否要打断上一轮未结束的任务由
     * RobotConnectionManager.sendIndoorInstruction 内部按 turn 状态决定（steer 还是 task），这里不再
     * 显式调用 cancelIndoorInstruction——那样会产生一个不等待、也不针对具体 turn 的 abort，容易打到
     * 刚提交的新指令上，导致新指令一下发就收到 interrupted。
     * 只把 final_text/error 写入 RobotConversationLog，text_chunk/plan/batch_result/node_state/
     * task_state/status 等中间态事件不展示也不播报，避免刷屏（参考 robonix-client-android 的
     * ChatViewModel.handlePilotEvent：只有最终结果和错误会进入可见的消息列表）。 */
    private fun dispatchIndoorInstruction(instruction: String) {
        RobotConversationLog.clear()
        dispatchInstructionToRobot(instruction)
    }

    private fun dispatchInstructionToRobot(instruction: String) {
        RobotConnectionManager.sendIndoorInstruction(
            text = instruction,
            onEvent = { event ->
                // 只把 final_text 写入可见记录；plan/batch_result/node_state/task_state/status 等中间态
                // 事件只用于 RobotConnectionManager 内部维护 turn 状态，不展示也不播报——参考
                // robonix-client-android 的 ChatViewModel.handlePilotEvent：这些事件只会落进它自己的
                // 二级 timeline（折叠、限容），从不进入主消息列表，否则同一状态反复下发会在列表里刷屏。
                if (event.kind == "final_text" && event.finalText.isNotBlank()) {
                    RobotConversationLog.append(instruction, event.finalText, event.kind)
                }
            },
            onError = { error -> RobotConversationLog.append(instruction, error, "error") },
        )
    }

    private fun sendIndoorManualInput() {
        val text = bind.etIndoorInput.text.toString().trim()
        if (text.isEmpty()) return
        bind.etIndoorInput.setText("")
        sendIndoorFreeformInstruction(text)
    }

    private fun handleIndoorTranscript(text: String) {
        updateIndoorVoiceButton(recording = false)
        sendIndoorFreeformInstruction(text)
    }

    /** 用户手动输入/语音下发的自由指令：结果追加进现有下发记录（不清空历史）。是否打断上一轮同样交给
     * RobotConnectionManager.sendIndoorInstruction 按 turn 状态决定，不在这里显式 abort。 */
    private fun sendIndoorFreeformInstruction(text: String) {
        dispatchInstructionToRobot(text)
    }

    private fun updateIndoorVoiceButton(recording: Boolean) {
        bind.btnIndoorVoice.setBackgroundResource(if (recording) R.drawable.bg_btn_recording else R.drawable.bg_btn_outline)
        bind.btnIndoorVoice.contentDescription = getString(if (recording) R.string.cd_agent_voice_btn_recording else R.string.cd_agent_voice_btn)
        if (recording) bind.root.announceA11y(getString(R.string.agent_voice_listening))
    }

    private fun handleIndoorVoiceError(reason: VoiceErrorReason) {
        updateIndoorVoiceButton(recording = false)
        val message = when (reason) {
            VoiceErrorReason.PERMISSION_DENIED -> getString(R.string.agent_voice_error_permission_denied)
            VoiceErrorReason.NO_SPEECH -> getString(R.string.agent_voice_error_no_speech)
            VoiceErrorReason.CONNECTION_FAILED -> getString(R.string.agent_voice_error_connection_failed)
            VoiceErrorReason.RECORDING_FAILED -> getString(R.string.agent_voice_error_recording_failed)
        }
        bind.root.announceA11y(message)
        ToastUtil.showShort(message)
        TtsUtils.speak(message)
    }

    /** 仅在用户手动点击室内"结束导航"后调用：中止机器狗当前任务，推进到计划下一阶段 */
    private fun finishIndoorPhase() {
        RobotConnectionManager.cancelIndoorInstruction()
        NavigationPlanManager.advanceToNextPhase()
    }

    /** 进入室外阶段：清空上一次残留的路线/导航状态，展示路线选择分组，用 LLM 解析出的目的地预填
     * 目的地框，并申请相机/定位权限。resetForRestart() 不会触发 onNavigationEnded，因此不会被
     * 误判为"用户点击了结束导航"而推进阶段。 */
    private fun enterOutdoorPhase(destination: String?) {
        outdoorController?.resetForRestart()
        outdoorNavigationActive.value = false
        showOutdoorSelectGroup()
        bind.etEnd.setText(destination.orEmpty())
        bind.tvStatus.text = ""
        bind.tvSuggestionStatus.text = ""
        bind.containerSuggestions.removeAllViews()
        ensureOutdoorPermissions()
    }

    private fun ensureOutdoorPermissions() {
        val controller = outdoorController ?: return
        val missing = mutableListOf<String>()
        if (!controller.hasCameraPermission()) missing += Manifest.permission.CAMERA
        if (!controller.hasLocationPermission()) {
            missing += Manifest.permission.ACCESS_FINE_LOCATION
            missing += Manifest.permission.ACCESS_COARSE_LOCATION
        }
        if (missing.isNotEmpty()) permissionLauncher.launch(missing.toTypedArray())
    }

    private fun showOutdoorSelectGroup() {
        bind.groupOutdoorSelect.visibility = View.VISIBLE
        bind.groupOutdoorActive.visibility = View.GONE
    }

    private fun showOutdoorActiveGroup() {
        bind.groupOutdoorSelect.visibility = View.GONE
        bind.groupOutdoorActive.visibility = View.VISIBLE
    }

    private fun colorFor(level: GuidanceLevel): Int = ContextCompat.getColor(
        requireContext(),
        when (level) {
            GuidanceLevel.SAFE -> R.color.color_success
            GuidanceLevel.WARNING -> R.color.color_warning
            GuidanceLevel.DANGER -> R.color.color_danger
            GuidanceLevel.MUTED -> R.color.color_text_muted
        }
    )

    private fun renderSuggestions(suggestions: List<PlaceSuggestion>) {
        bind.containerSuggestions.removeAllViews()
        bind.tvSuggestionStatus.text = if (suggestions.isEmpty()) "" else getString(R.string.nav_suggestion_status_searching)
        suggestions.forEach { suggestion ->
            val row = TextView(context).apply {
                text = "${suggestion.name} · ${suggestion.address}（${suggestion.distanceMeters}m）"
                setTextColor(ContextCompat.getColor(requireContext(), R.color.color_text))
                textSize = 13f
                setBackgroundResource(R.drawable.bg_card)
                setPadding(
                    resources.getDimensionPixelSize(R.dimen.dp_12),
                    resources.getDimensionPixelSize(R.dimen.dp_10),
                    resources.getDimensionPixelSize(R.dimen.dp_12),
                    resources.getDimensionPixelSize(R.dimen.dp_10)
                )
                setOnClickListener {
                    bind.etEnd.setText(suggestion.name)
                    outdoorController?.selectSuggestion(suggestion)
                }
            }
            val params = android.widget.LinearLayout.LayoutParams(
                android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT
            )
            params.topMargin = resources.getDimensionPixelSize(R.dimen.dp_6)
            row.layoutParams = params
            bind.containerSuggestions.addView(row)
        }
    }

    private fun showVisionHintSettingsDialog() {
        val controller = outdoorController ?: return
        val current = controller.visionHintSettings()
        val density = resources.displayMetrics.density
        val padding = (20 * density).toInt()
        val container = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding, padding / 2, padding, 0)
        }
        fun field(hint: String, value: String, password: Boolean = false) =
            EditText(requireContext()).apply {
                this.hint = hint
                setText(value)
                setSingleLine(true)
                if (password) {
                    inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
                }
                container.addView(this)
            }
        val endpoint = field("HTTPS 兼容接口地址", current.endpoint)
        val key = field("API Key", current.key, password = true)
        val model = field("模型名称", current.model)

        androidx.appcompat.app.AlertDialog.Builder(requireContext())
            .setTitle("视觉提示设置")
            .setMessage("仅用于补充五方向物体描述，不参与导航方向、避障或安全决策。")
            .setView(container)
            .setNegativeButton("取消", null)
            .setPositiveButton("保存") { _, _ ->
                try {
                    controller.updateVisionHintSettings(
                        VisionHintSettings(
                            enabled = bind.switchVisionHints.isChecked,
                            key = key.text.toString(),
                            endpoint = endpoint.text.toString(),
                            model = model.text.toString()))
                } catch (error: IllegalArgumentException) {
                    Toast.makeText(
                        requireContext(),
                        error.message ?: "接口地址无效",
                        Toast.LENGTH_LONG).show()
                }
            }
            .show()
    }
    /** 仅在用户手动点击"结束导航"后调用：回到路线选择分组，推进到 LLM 计划的下一阶段 */
    private fun finishOutdoorPhase() {
        outdoorNavigationActive.value = false
        showOutdoorSelectGroup()
        NavigationPlanManager.advanceToNextPhase()
    }

    private inner class OutdoorListener : OutdoorNavController.Listener {
        override fun onCameraStatus(text: String) {
            bind.tvCameraStatus.text = text
            val deviceStatus = when {
                text.contains("已连接") || text.contains("运行中") -> "online"
                text.contains("已断开") -> "offline"
                else -> "error"
            }
            DeviceWatchClient.reportDeviceStatus(DeviceType.DEPTH_CAMERA,"深度相机",deviceStatus)
        }

        override fun onGuidanceChanged(text: String, level: GuidanceLevel) {
            bind.tvGuidance.text = text.ifBlank { getString(R.string.nav_voice_placeholder) }
            bind.tvGuidance.setTextColor(colorFor(level))
            // 内容不变时跳过播报：guidance 刷新频率很高，若每次都打断重播会导致语音持续被截断、
            // 一句话都念不完整。
            if (text.isNotBlank() && text != lastSpokenGuidance) {
                lastSpokenGuidance = text
                TtsUtils.speak(text, interrupt = true)
            }
        }

        override fun onDistances(left: String, center: String, right: String) {
            bind.tvDistanceLeft.text = left
            bind.tvDistanceCenter.text = center
            bind.tvDistanceRight.text = right
        }

        override fun onSemanticOverlay(text: String, level: GuidanceLevel) {
            bind.tvSemanticStatus.text = text
            bind.tvSemanticStatus.setTextColor(colorFor(level))
        }

        override fun onLocalPlanMetrics(text: String, level: GuidanceLevel) {
            bind.tvLocalPlanMetrics.text = text
            bind.tvLocalPlanMetrics.setTextColor(colorFor(level))
        }

        override fun onLocalPlan(plan: LocalPlanSnapshot) {
            bind.tvLocalPlan.text = when {
                plan.blocked -> "局部规划：前方受阻"
                plan.planned && plan.success -> "局部规划：转向 %+.0f°".format(plan.steeringDegrees)
                plan.planned -> "局部规划：规划失败"
                else -> plan.waitingReason ?: "局部规划：等待中"
            }
            bind.localPlanView.setPlan(
                plan.visualizationGrid,
                plan.waitingReason,
                plan.cameraSeconds ?: Double.NaN,
                plan.captureElapsedMillis,
                plan.frameGeneration)
        }

        override fun onHeading(headingDegrees: Float, text: String) {
            bind.tvHeading.text = text
        }

        override fun onLocation(location: Location, text: String) {
            bind.tvLocation.text = text
        }

        override fun onLocationStatus(text: String) {
            bind.tvLocationStatus.text = text
        }

        override fun onSuggestions(suggestions: List<PlaceSuggestion>) {
            renderSuggestions(suggestions)
        }

        override fun onRouteStatus(text: String) {
            bind.tvStatus.text = text
        }

        override fun onNavigationStatus(text: String, visible: Boolean) {
            bind.tvNavStatus.text = text
            bind.tvNavStatus.visibility = if (visible) View.VISIBLE else View.GONE
        }

        override fun onFrameStatus(text: String) {
            bind.tvFrameStatus.text = text
        }

        override fun onCalibrationStatus(text: String, calibrated: Boolean, recalibrationRequired: Boolean) {
            bind.tvCalibrationStatus.text = text
            bind.tvCalibrationStatus.setTextColor(colorFor(
                if (calibrated) GuidanceLevel.SAFE else GuidanceLevel.MUTED))
            bind.btnCalibrateHeading.visibility =
                if (recalibrationRequired) View.VISIBLE else View.GONE
            bind.btnCalibrateHeading.isEnabled = recalibrationRequired
            bind.btnCalibrateHeading.alpha = if (recalibrationRequired) 1f else 0.5f
        }

        override fun onNavigationStarted() {
            outdoorNavigationActive.value = true
            lastSpokenGuidance = null
            showOutdoorActiveGroup()
        }

        override fun onNavigationEnded() {
            finishOutdoorPhase()
        }

        /** 到达目的地仅提示，不自动推进阶段：需用户手动点击"结束导航"才跳转到下一步 */
        override fun onArrived() {
            bind.tvNavStatus.text = getString(R.string.nav_arrived_toast)
            bind.tvNavStatus.visibility = View.VISIBLE
            TtsUtils.speak(getString(R.string.nav_arrived_toast))
        }

        override fun onDepthPreview(bitmap: android.graphics.Bitmap?) {
            bind.ivDepthPreview.setImageBitmap(bitmap)
        }

        override fun onColorPreview(bitmap: android.graphics.Bitmap?) {
            bind.ivColorPreview.setImageBitmap(bitmap)
            bitmap?.let { DeviceWatchClient.pushDeviceFrame(DeviceType.DEPTH_CAMERA, it) }
        }

        override fun onVinsStatus(text: String, level: GuidanceLevel) {
            bind.tvVinsStatus.text = text
            bind.tvVinsStatus.setTextColor(colorFor(level))
        }

        override fun onVisionHints(primary: String, diagnostic: String, enabled: Boolean) {
            bind.switchVisionHints.isChecked = enabled
            bind.tvVisionHints.text = primary.ifBlank {
                "左侧：无\n左前方：无\n正前方：无\n右前方：无\n右侧：无"
            }
            bind.tvVisionDiagnostic.text = diagnostic
        }
    }
}
