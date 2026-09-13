package com.robonix.client.data.model

data class RtdlPlan(
    val planId: String,
    val sessionId: String,
    val round: Int,
    val rootIndex: Int,
    val nodes: List<RtdlNode>,
)

data class RtdlNode(
    val index: Int,
    val kindId: Int,
    val kind: String,
    val children: List<Int>,
    val opId: String = "",
    val description: String = "",
    val call: CapabilityCall? = null,
)

data class CapabilityCall(
    val callId: String,
    val providerId: String,
    val contractId: String,
    val name: String,
    val args: Map<String, Any?> = emptyMap(),
)

data class RtdlNodeState(
    val nodeIndex: Int,
    val state: String,
    val leafResult: CapabilityCallResult? = null,
    val planId: String = "",
    val opId: String = "",
    val description: String = "",
)

data class CapabilityCallResult(
    val callId: String,
    val providerId: String,
    val contractId: String,
    val name: String,
    val success: Boolean,
    val output: String,
    val error: String,
)

data class BatchResult(
    val planId: String,
    val sessionId: String,
    val round: Int,
    val anyFailed: Boolean,
    val results: List<RtdlNodeState>,
)

data class TaskState(
    val goal: String = "",
    val successCriterion: String = "",
    val status: String = "idle",
)

data class ExecutorPlan(
    val planId: String,
    val description: String,
    val opCount: Int,
    val cancelled: Boolean,
    val ops: List<ExecutorOp>,
)

data class ExecutorOp(
    val opId: String,
    val description: String,
    val kind: String,
    val state: String,
    val providerId: String,
    val contractId: String,
)
