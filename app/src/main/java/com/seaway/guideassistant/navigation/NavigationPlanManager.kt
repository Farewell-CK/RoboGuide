package com.seaway.guideassistant.navigation

import com.lsxiao.apollo.core.Apollo
import com.seaway.guideassistant.llm.NavigationIntent
import com.seaway.guideassistant.llm.NavigationPhase
import com.seaway.guideassistant.utils.ApolloEvents
import com.seaway.guideassistant.ws.DeviceWatchClient
import com.seaway.guideassistant.ws.NavigationPhaseData
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * 多阶段出行计划的唯一持有者。将大模型解析出的 [NavigationIntent.navigationPhases]
 * 保存为按索引推进的计划，驱动 NavigateFragment 在室内/室外模式之间切换。
 */
object NavigationPlanManager {

    data class NavigationPlan(
        val destination: String?,
        val phases: List<NavigationPhase>,
        val rawInstruction: String
    )

    sealed class PlanState {
        object Idle : PlanState()
        data class InProgress(val plan: NavigationPlan, val phaseIndex: Int, val planId: Int) : PlanState()
        object Completed : PlanState()
    }

    val state = MutableStateFlow<PlanState>(PlanState.Idle)

    /** 每次 [startPlan] 重新下发都会自增，供 NavigateFragment 区分"新指令"与"同一计划内的阶段推进" */
    private var nextPlanId = 0

    fun currentPhase(): NavigationPhase? =
        (state.value as? PlanState.InProgress)?.let { it.plan.phases[it.phaseIndex] }

    /** Agent 侧解析出多阶段导航意图后调用：保存计划 + 跳转到出行 Tab。
     * [rawInstruction] 是 AgentFragment 下发给大模型的完整原始指令文本，供 NavigateFragment 展示。 */
    fun startPlan(intent: NavigationIntent, rawInstruction: String) {
        if (intent.intent != "navigation" || intent.needClarification || intent.navigationPhases.isEmpty()) return
        val sortedPhases = intent.navigationPhases.sortedBy { it.phase }
        DeviceWatchClient.reportNavigation(
            instruction = rawInstruction,
            destination = intent.destination.orEmpty(),
            phases = sortedPhases.map { NavigationPhaseData(it.phase, it.mode, it.description, it.floor) },
            currentPhase = sortedPhases.first().phase,
            totalPhases = sortedPhases.size,
            needClarification = intent.needClarification,
            clarificationQuestion = intent.clarificationQuestion,
            confidence = intent.confidence?.toFloat() ?: 0f
        )
        val plan = NavigationPlan(intent.destination, sortedPhases, rawInstruction)
        state.value = PlanState.InProgress(plan, 0, ++nextPlanId)
        Apollo.emit(ApolloEvents.SWITCH_TO_NAVIGATE_TAB)
    }

    /** 由 NavigateFragment 在当前阶段完成后调用：仅手动点击"结束导航"会走到这里，到达目的地不会自动推进 */
    fun advanceToNextPhase() {
        val s = state.value as? PlanState.InProgress ?: return
        val next = s.phaseIndex + 1
        state.value = if (next < s.plan.phases.size) s.copy(phaseIndex = next) else PlanState.Completed
    }

    fun cancel() {
        state.value = PlanState.Idle
    }
}
