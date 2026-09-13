package com.robonix.client.domain

import android.util.Log
import com.robonix.client.data.model.BatchResult
import com.robonix.client.data.model.RtdlNodeState
import com.robonix.client.data.model.RtdlPlan
import com.robonix.client.ui.chat.PlanRecord
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Shared RTDL state holder — injected as a plain @Singleton so both
 * ChatViewModel and RtdlViewModel can read/write plan data without
 * violating Hilt's cross-ViewModel injection rules.
 */
@Singleton
class RtdlStateHolder @Inject constructor() {
    private val _livePlan = MutableStateFlow<RtdlPlan?>(null)
    val livePlan: StateFlow<RtdlPlan?> = _livePlan.asStateFlow()

    private val _liveNodeStates = MutableStateFlow<Map<Int, RtdlNodeState>>(emptyMap())
    val liveNodeStates: StateFlow<Map<Int, RtdlNodeState>> = _liveNodeStates.asStateFlow()

    private val _liveBatches = MutableStateFlow<List<BatchResult>>(emptyList())
    val liveBatches: StateFlow<List<BatchResult>> = _liveBatches.asStateFlow()

    private val _planRecords = MutableStateFlow<List<PlanRecord>>(emptyList())
    val planRecords: StateFlow<List<PlanRecord>> = _planRecords.asStateFlow()

    /** Increments whenever a NEW plan record appears — drives the RTDL unread badge. */
    private val _planVersion = MutableStateFlow(0L)
    val planVersion: StateFlow<Long> = _planVersion.asStateFlow()

    fun update(
        plan: RtdlPlan?,
        nodeStates: Map<Int, RtdlNodeState>,
        batches: List<BatchResult>,
        records: List<PlanRecord>,
    ) {
        Log.w("RobonixRtdl", "RtdlStateHolder.update: plan=${plan?.planId} nodes=${plan?.nodes?.size} " +
            "nodeStates=${nodeStates.size} batches=${batches.size} records=${records.size}")
        _livePlan.value = plan
        _liveNodeStates.value = nodeStates
        _liveBatches.value = batches
        if (records.size > _planRecords.value.size) _planVersion.value += 1
        _planRecords.value = records
    }

    fun clear() {
        _livePlan.value = null
        _liveNodeStates.value = emptyMap()
        _liveBatches.value = emptyList()
        _planRecords.value = emptyList()
    }
}
