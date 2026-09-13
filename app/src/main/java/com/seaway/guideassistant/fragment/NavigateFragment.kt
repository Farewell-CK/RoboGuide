package com.seaway.guideassistant.fragment

import android.Manifest
import android.location.Location
import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.widget.doOnTextChanged
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.elabrador.mobilenavigation.GuidanceLevel
import com.elabrador.mobilenavigation.LocalPlanSnapshot
import com.elabrador.mobilenavigation.OutdoorNavController
import com.elabrador.mobilenavigation.PlaceSuggestion
import com.seaway.guideassistant.R
import com.seaway.guideassistant.base.BaseBindFragment
import com.seaway.guideassistant.base.Constant
import com.seaway.guideassistant.databinding.FragmentNavigateBinding
import com.seaway.guideassistant.navigation.NavigationPlanManager
import com.seaway.guideassistant.robot.RobotConnectionManager
import com.seaway.guideassistant.robot.RobotConversationLog
import com.seaway.guideassistant.utils.announceA11y
import com.seaway.smallutils.TtsUtils
import kotlinx.coroutines.launch

class NavigateFragment : BaseBindFragment<FragmentNavigateBinding>() {

    private var outdoorController: OutdoorNavController? = null
    private var lastDispatchedPhaseIndex = -1
    private var lastAnnouncedCompleted = false
    private var lastSpokenGuidance: String? = null

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
        }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        outdoorController = OutdoorNavController(requireContext(), Constant.AMAP_KEY, OutdoorListener())
        bind.btnPlanRoute.setOnClickListener { outdoorController?.planRoute(bind.etEnd.text.toString()) }
        bind.btnStartStop.setOnClickListener { outdoorController?.endNavigation() }
        bind.btnCalibrateHeading.setOnClickListener { outdoorController?.calibrateHeading() }
        bind.etEnd.doOnTextChanged { text, _, _, _ -> outdoorController?.searchDestination(text?.toString().orEmpty()) }
        observePlan()
        observeRobotStatus()
        observeRobotLog()
    }

    override fun onResume() {
        super.onResume()
        outdoorController?.onResume()
    }

    override fun onPause() {
        outdoorController?.onPause()
        super.onPause()
    }

    override fun onDestroyView() {
        outdoorController?.onDestroy()
        outdoorController = null
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
        }
    }

    private fun renderPlanState(state: NavigationPlanManager.PlanState) {
        val inProgress = state as? NavigationPlanManager.PlanState.InProgress
        val phase = inProgress?.let { it.plan.phases[it.phaseIndex] }

        if (inProgress != null && phase != null) {
            bind.tvPhaseStatus.visibility = View.VISIBLE
            bind.tvPhaseStatus.text = getString(
                R.string.nav_phase_status_fmt,
                inProgress.phaseIndex + 1, inProgress.plan.phases.size, phase.description
            )
        } else {
            bind.tvPhaseStatus.visibility = View.GONE
        }

        val showIndoor = phase?.mode == "indoor"
        bind.groupIndoorMode.visibility = if (showIndoor) View.VISIBLE else View.GONE
        bind.groupOutdoorMode.visibility = if (showIndoor) View.GONE else View.VISIBLE

        if (inProgress != null && inProgress.phaseIndex != lastDispatchedPhaseIndex) {
            lastDispatchedPhaseIndex = inProgress.phaseIndex
            if (showIndoor) {
                dispatchIndoorInstruction(phase!!.description)
            } else {
                enterOutdoorPhase(inProgress.plan.destination)
            }
        }

        if (state is NavigationPlanManager.PlanState.Completed) {
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
        }
    }

    /** 把当前室内阶段的指令下发给机器狗，结果写入 RobotConversationLog 供上方列表展示 */
    private fun dispatchIndoorInstruction(instruction: String) {
        RobotConnectionManager.sendIndoorInstruction(
            text = instruction,
            onEvent = { event ->
                val summary = event.finalText.ifBlank { event.textChunk.ifBlank { event.status?.message.orEmpty() } }
                RobotConversationLog.append(instruction, summary.ifBlank { event.kind }, event.kind)
            },
            onError = { error -> RobotConversationLog.append(instruction, error, "error") },
        )
    }

    /** 进入室外阶段：展示路线选择分组，用 LLM 解析出的目的地预填目的地框，并申请相机/定位权限 */
    private fun enterOutdoorPhase(destination: String?) {
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

    /** 仅在用户手动点击"结束导航"后调用：回到路线选择分组，推进到 LLM 计划的下一阶段 */
    private fun finishOutdoorPhase() {
        showOutdoorSelectGroup()
        NavigationPlanManager.advanceToNextPhase()
    }

    private inner class OutdoorListener : OutdoorNavController.Listener {
        override fun onCameraStatus(text: String) {
            bind.tvCameraStatus.text = text
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

        override fun onLocalPlan(plan: LocalPlanSnapshot) {
            bind.tvLocalPlan.text = when {
                plan.blocked -> "局部规划：前方受阻"
                plan.planned && plan.success -> "局部规划：转向 %+.0f°".format(plan.steeringDegrees)
                plan.planned -> "局部规划：规划失败"
                else -> plan.waitingReason ?: "局部规划：等待中"
            }
            bind.localPlanView.setPlan(plan.visualizationGrid, plan.waitingReason)
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

        override fun onCalibrationStatus(text: String, ready: Boolean) {
            bind.tvCalibrationStatus.text = text
            bind.btnCalibrateHeading.isEnabled = ready
            bind.btnCalibrateHeading.alpha = if (ready) 1f else 0.5f
        }

        override fun onNavigationStarted() {
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

        override fun onVinsStatus(text: String, level: GuidanceLevel) {
            bind.tvVinsStatus.text = text
            bind.tvVinsStatus.setTextColor(colorFor(level))
        }
    }
}
