package com.robonix.client.data.grpc

import com.robonix.client.data.model.BatchResult
import com.robonix.client.data.model.CapabilityCall
import com.robonix.client.data.model.CapabilityCallResult
import com.robonix.client.data.model.PilotEvent
import com.robonix.client.data.model.RtdlNode
import com.robonix.client.data.model.RtdlNodeState
import com.robonix.client.data.model.RtdlPlan
import com.robonix.client.data.model.SessionStatus
import com.robonix.client.data.model.TaskState
import com.robonix.client.data.model.HandsfreeStatus
import com.robonix.client.data.model.VoiceEvent
import com.robonix.proto.liaison.FinishVoiceCapture_Request
import com.robonix.proto.liaison.FinishVoiceCapture_Response
import com.robonix.proto.liaison.GetHandsfreeStatus_Request
import com.robonix.proto.liaison.GetHandsfreeStatus_Response
import com.robonix.proto.liaison.SetHandsfree_Request
import com.robonix.proto.liaison.SetHandsfree_Response
import com.robonix.proto.liaison.StartVoiceSession_Request
import com.robonix.proto.liaison.StartVoiceSession_Response
import com.robonix.proto.liaison.WatchHandsfreeEvents_Request
import com.robonix.proto.liaison.WatchHandsfreeEvents_Response
import com.robonix.client.AppLog
import com.robonix.proto.liaison.VoiceEvent as ProtoVoiceEvent
import com.robonix.proto.pilot.PilotEvent as ProtoPilotEvent
import com.robonix.proto.pilot.Task as ProtoTask
import io.grpc.MethodDescriptor
import io.grpc.protobuf.ProtoUtils
import io.grpc.stub.ClientCalls
import io.grpc.stub.StreamObserver
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import org.json.JSONObject
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class LiaisonClient @Inject constructor(
    private val channelProvider: GrpcChannelProvider,
) {
    // --- Contract-based gRPC method paths (matching robonix backend exactly) ---

    private val SUBMIT_TASK_METHOD: MethodDescriptor<ProtoTask, ProtoPilotEvent> =
        MethodDescriptor.newBuilder<ProtoTask, ProtoPilotEvent>()
            .setType(MethodDescriptor.MethodType.SERVER_STREAMING)
            .setFullMethodName("robonix.contracts.RobonixSystemLiaisonSubmit/SubmitTask")
            .setRequestMarshaller(ProtoUtils.marshaller(
                ProtoTask.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(
                ProtoPilotEvent.getDefaultInstance()))
            .build()

    private val START_VOICE_METHOD: MethodDescriptor<StartVoiceSession_Request, StartVoiceSession_Response> =
        MethodDescriptor.newBuilder<StartVoiceSession_Request, StartVoiceSession_Response>()
            .setType(MethodDescriptor.MethodType.SERVER_STREAMING)
            .setFullMethodName("robonix.contracts.RobonixSystemLiaisonVoice/StartVoiceSession")
            .setRequestMarshaller(ProtoUtils.marshaller(
                StartVoiceSession_Request.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(
                StartVoiceSession_Response.getDefaultInstance()))
            .build()

    private val FINISH_VOICE_METHOD: MethodDescriptor<FinishVoiceCapture_Request, FinishVoiceCapture_Response> =
        MethodDescriptor.newBuilder<FinishVoiceCapture_Request, FinishVoiceCapture_Response>()
            .setType(MethodDescriptor.MethodType.UNARY)
            .setFullMethodName("robonix.contracts.RobonixSystemLiaisonVoiceFinish/FinishVoiceCapture")
            .setRequestMarshaller(ProtoUtils.marshaller(
                FinishVoiceCapture_Request.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(
                FinishVoiceCapture_Response.getDefaultInstance()))
            .build()

    private val HANDSFREE_STATUS_METHOD: MethodDescriptor<GetHandsfreeStatus_Request, GetHandsfreeStatus_Response> =
        MethodDescriptor.newBuilder<GetHandsfreeStatus_Request, GetHandsfreeStatus_Response>()
            .setType(MethodDescriptor.MethodType.UNARY)
            .setFullMethodName("robonix.contracts.RobonixSystemLiaisonHandsfreeStatus/GetHandsfreeStatus")
            .setRequestMarshaller(ProtoUtils.marshaller(
                GetHandsfreeStatus_Request.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(
                GetHandsfreeStatus_Response.getDefaultInstance()))
            .build()

    private val HANDSFREE_SET_METHOD: MethodDescriptor<SetHandsfree_Request, SetHandsfree_Response> =
        MethodDescriptor.newBuilder<SetHandsfree_Request, SetHandsfree_Response>()
            .setType(MethodDescriptor.MethodType.UNARY)
            .setFullMethodName("robonix.contracts.RobonixSystemLiaisonHandsfreeSetEnabled/SetHandsfree")
            .setRequestMarshaller(ProtoUtils.marshaller(
                SetHandsfree_Request.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(
                SetHandsfree_Response.getDefaultInstance()))
            .build()

    private val HANDSFREE_EVENTS_METHOD: MethodDescriptor<WatchHandsfreeEvents_Request, WatchHandsfreeEvents_Response> =
        MethodDescriptor.newBuilder<WatchHandsfreeEvents_Request, WatchHandsfreeEvents_Response>()
            .setType(MethodDescriptor.MethodType.SERVER_STREAMING)
            .setFullMethodName("robonix.contracts.RobonixSystemLiaisonHandsfreeEvents/WatchHandsfreeEvents")
            .setRequestMarshaller(ProtoUtils.marshaller(
                WatchHandsfreeEvents_Request.getDefaultInstance()))
            .setResponseMarshaller(ProtoUtils.marshaller(
                WatchHandsfreeEvents_Response.getDefaultInstance()))
            .build()

    fun submitTask(
        target: String,
        text: String,
        sessionId: String,
        userId: String = "local:robonix-android",
        steer: Boolean = false,
        expectedTurnId: String = "",
        isAbort: Boolean = false,
    ): Flow<PilotEvent> = callbackFlow {
        val channel = channelProvider.getChannel(target)

        val context = JSONObject().apply {
            put("user_id", userId)
            put("client", "robonix-client-android")
            when {
                isAbort -> {
                    put("abort_turn", true)
                    put("interaction_mode", "abort")
                    if (expectedTurnId.isNotBlank()) put("expected_turn_id", expectedTurnId)
                }
                steer -> {
                    put("modality", "text")
                    put("interaction_mode", "steer")
                    put("steer", true)
                    if (expectedTurnId.isNotBlank()) put("expected_turn_id", expectedTurnId)
                }
                else -> {
                    put("modality", "text")
                    put("interaction_mode", "task")
                }
            }
        }

        val task = ProtoTask.newBuilder()
            .setTaskId(UUID.randomUUID().toString())
            .setSessionId(sessionId)
            .setSource(0)
            .setText(text)
            .setContextJson(context.toString())
            .setTimestampMs(System.currentTimeMillis())
            .build()

        val call = channel.newCall(SUBMIT_TASK_METHOD, io.grpc.CallOptions.DEFAULT)
        ClientCalls.asyncServerStreamingCall(
            call,
            task,
            object : StreamObserver<ProtoPilotEvent> {
                override fun onNext(event: ProtoPilotEvent) {
                    val evt = mapPilotEvent(event)
                    AppLog.write("GPRC", "submitTask onNext: kind=${evt.kind}")
                    trySend(evt)
                }
                override fun onError(t: Throwable) {
                    AppLog.write("GPRC", "submitTask onError: ${t.message}", t)
                    trySend(PilotEvent(kind = "error", textChunk = t.message ?: "gRPC error"))
                    close(t)
                }
                override fun onCompleted() {
                    AppLog.write("GPRC", "submitTask onCompleted")
                    close()
                }
            },
        )

        awaitClose { call.cancel("flow collector cancelled", null) }
    }

    fun startVoiceSession(
        target: String,
        sessionId: String,
        userId: String,
        recordSeconds: Int = 30,
        language: String = "",
        micNodeId: String = "",
        asrNodeId: String = "",
        voiceprintNodeId: String = "",
        ttsNodeId: String = "",
        speakerNodeId: String = "",
        steer: Boolean = false,
        expectedTurnId: String = "",
    ): Flow<VoiceEvent> = callbackFlow {
        val channel = channelProvider.getChannel(target)

        val context = JSONObject().apply {
            put("client", "robonix-client-android")
            put("interaction_mode", if (steer) "steer" else "voice")
            put("barge_in", true)
            if (steer) {
                put("steer", true)
                if (expectedTurnId.isNotBlank()) put("expected_turn_id", expectedTurnId)
            }
        }

        val request = StartVoiceSession_Request.newBuilder()
            .setSessionId(sessionId)
            .setClientUserId(userId)
            .setRecordSeconds(recordSeconds)
            .setLanguage(language)
            .setTtsEnabled(true)
            .setMicNodeId(micNodeId)
            .setAsrNodeId(asrNodeId)
            .setVoiceprintNodeId(voiceprintNodeId)
            .setTtsNodeId(ttsNodeId)
            .setSpeakerNodeId(speakerNodeId)
            .setContextJson(context.toString())
            .build()

        val call = channel.newCall(START_VOICE_METHOD, io.grpc.CallOptions.DEFAULT)
        ClientCalls.asyncServerStreamingCall(
            call,
            request,
            object : StreamObserver<StartVoiceSession_Response> {
                override fun onNext(wrapped: StartVoiceSession_Response) {
                    // Try the wrapper format first (current liaison builds)
                    if (wrapped.hasEvent()) {
                        val evt = mapVoiceEvent(wrapped.event)
                        trySend(evt)
                        return
                    }
                    // Fallback: older liaison builds stream raw VoiceEvent bytes,
                    // not wrapped in StartVoiceSession_Response. The proto3 parser
                    // stores them as unknown fields with hasEvent() == false.
                    // This matches the web client's decode_voice_event pattern.
                    try {
                        val raw = ProtoVoiceEvent.parseFrom(wrapped.toByteArray())
                        trySend(mapVoiceEvent(raw))
                    } catch (_: Exception) {
                        AppLog.write("VOICE", "Voice onNext: hasEvent=false, raw parse also failed, size=${wrapped.serializedSize}")
                    }
                }
                override fun onError(t: Throwable) {
                    com.robonix.client.AppLog.write("VOICE", "gRPC voice stream error: ${t.message}", t)
                    trySend(VoiceEvent(kind = "error", error = t.message ?: "voice error"))
                    close(t)
                }
                override fun onCompleted() {
                    com.robonix.client.AppLog.write("VOICE", "gRPC voice stream completed")
                    close()
                }
            },
        )

        awaitClose { call.cancel("flow collector cancelled", null) }
    }

    /**
     * Early-finish a running voice session: the server stops recording and
     * submits the recognized-so-far text. The voice stream itself keeps
     * running afterwards (it flushes asr_final / session_done events).
     * Matches the web client's finish_voice_capture call.
     */
    suspend fun finishVoiceCapture(
        target: String,
        sessionId: String,
    ): Result<String> = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
        try {
            val channel = channelProvider.getChannel(target)
            val response = ClientCalls.blockingUnaryCall(
                channel.newCall(FINISH_VOICE_METHOD, io.grpc.CallOptions.DEFAULT),
                FinishVoiceCapture_Request.newBuilder().setSessionId(sessionId).build(),
            )
            if (response.ok) Result.success(response.detail.ifBlank { response.sessionId })
            else Result.failure(RuntimeException(response.detail.ifBlank { "finish rejected" }))
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    // --- Handsfree (Phase 4) ---

    suspend fun getHandsfreeStatus(target: String): HandsfreeStatus =
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            val channel = channelProvider.getChannel(target)
            val resp = ClientCalls.blockingUnaryCall(
                channel.newCall(HANDSFREE_STATUS_METHOD, io.grpc.CallOptions.DEFAULT),
                GetHandsfreeStatus_Request.getDefaultInstance(),
            )
            HandsfreeStatus(
                enabled = resp.enabled,
                state = resp.state,
                keyword = resp.keyword,
                micProviderId = resp.micProviderId,
                speakerProviderId = resp.speakerProviderId,
                lastWakeMs = resp.lastWakeMs,
                lastTranscript = resp.lastTranscript,
                lastError = resp.lastError,
            )
        }

    suspend fun setHandsfreeEnabled(
        target: String,
        enabled: Boolean,
        micProviderId: String = "",
        speakerProviderId: String = "",
    ): Result<HandsfreeStatus> = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
        try {
            val channel = channelProvider.getChannel(target)
            val resp = ClientCalls.blockingUnaryCall(
                channel.newCall(HANDSFREE_SET_METHOD, io.grpc.CallOptions.DEFAULT),
                SetHandsfree_Request.newBuilder()
                    .setEnabled(enabled)
                    .setMicProviderId(micProviderId)
                    .setSpeakerProviderId(speakerProviderId)
                    .build(),
            )
            if (resp.ok) Result.success(HandsfreeStatus(
                enabled = resp.enabled,
                state = resp.state,
                // caller's provider ids — the response only carries ok/enabled/state/detail
                micProviderId = micProviderId,
                speakerProviderId = speakerProviderId,
            ))
            else Result.failure(RuntimeException(resp.detail.ifBlank { "set handsfree failed" }))
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    /** Server stream of handsfree voice events; closed automatically on collect-cancel. */
    fun watchHandsfreeEvents(target: String): Flow<VoiceEvent> = callbackFlow {
        val channel = channelProvider.getChannel(target)
        val call = channel.newCall(HANDSFREE_EVENTS_METHOD, io.grpc.CallOptions.DEFAULT)
        ClientCalls.asyncServerStreamingCall(
            call,
            WatchHandsfreeEvents_Request.getDefaultInstance(),
            object : StreamObserver<WatchHandsfreeEvents_Response> {
                override fun onNext(value: WatchHandsfreeEvents_Response) {
                    if (value.hasEvent()) trySend(mapVoiceEvent(value.event))
                }
                override fun onError(t: Throwable) {
                    AppLog.write("HANDSFREE", "events stream error: ${t.message}", t)
                    close(t)
                }
                override fun onCompleted() { close() }
            },
        )
        awaitClose { call.cancel("flow collector cancelled", null) }
    }

    // --- Proto mapping helpers ---

    private fun mapPilotEvent(event: ProtoPilotEvent): PilotEvent {
        val kindNames = mapOf(
            0 to "text_chunk", 1 to "plan", 2 to "batch_result",
            3 to "status", 4 to "final_text", 5 to "node_state", 6 to "task_state",
        )
        return PilotEvent(
            kind = kindNames[event.eventKind] ?: "unknown_${event.eventKind}",
            sessionId = event.sessionId,
            textChunk = event.textChunk,
            finalText = event.finalText,
            plan = if (event.hasPlan()) mapPlan(event.plan) else null,
            batchResult = if (event.hasBatchResult()) mapBatchResult(event.batchResult) else null,
            nodeState = if (event.hasNodeState()) mapNodeState(event.nodeState) else null,
            taskState = if (event.hasTaskState()) TaskState(
                goal = event.taskState.goal,
                successCriterion = event.taskState.successCriterion,
                status = event.taskState.status,
            ) else null,
            status = if (event.hasStatus()) SessionStatus(
                sessionId = event.status.sessionId,
                state = event.status.state,
                message = event.status.message,
            ) else null,
        )
    }

    private fun mapVoiceEvent(event: ProtoVoiceEvent): VoiceEvent {
        val kindNames = mapOf(
            0 to "session_started", 1 to "recording_started", 2 to "recording_done",
            3 to "asr_partial", 4 to "asr_final", 5 to "user_identified",
            6 to "pilot", 7 to "tts_started", 8 to "tts_done",
            9 to "session_done", 10 to "error",
        )
        return VoiceEvent(
            kind = kindNames[event.eventKind] ?: "unknown_${event.eventKind}",
            sessionId = event.sessionId,
            text = event.text,
            userId = event.userId,
            confidence = event.confidence,
            pilot = if (event.hasPilot()) mapPilotEvent(event.pilot) else null,
            error = event.error,
            statusMessage = event.statusMessage,
            timestampMs = event.timestampMs,
        )
    }

    private fun mapPlan(plan: com.robonix.proto.pilot.Plan): RtdlPlan {
        val nodeKindNames = mapOf(0 to "sequence", 1 to "parallel", 2 to "do")
        return RtdlPlan(
            planId = plan.planId,
            sessionId = plan.sessionId,
            round = plan.round,
            rootIndex = plan.rootIndex,
            nodes = plan.nodesList.mapIndexed { index, node ->
                RtdlNode(
                    index = index,
                    kindId = node.nodeKind,
                    kind = nodeKindNames[node.nodeKind] ?: "kind_${node.nodeKind}",
                    children = node.childrenList,
                    opId = node.opId,
                    description = node.description,
                    call = if (node.hasCall()) mapCapabilityCall(node.call) else null,
                )
            },
        )
    }

    private fun mapCapabilityCall(call: com.robonix.proto.pilot.CapabilityCall) = CapabilityCall(
        callId = call.callId,
        providerId = call.providerId,
        contractId = call.contractId,
        name = call.contractId.substringAfterLast("/"),
        args = try {
            val json = JSONObject(call.argsJson)
            json.keys().asSequence().associateWith { json.get(it) }
        } catch (_: Exception) {
            emptyMap()
        },
    )

    private fun mapBatchResult(batch: com.robonix.proto.pilot.BatchResult) = BatchResult(
        planId = batch.planId,
        sessionId = batch.sessionId,
        round = batch.round,
        anyFailed = batch.anyFailed,
        results = batch.resultsList.map { mapNodeState(it) },
    )

    private fun mapNodeState(state: com.robonix.proto.pilot.RtdlNodeState): RtdlNodeState {
        val stateNames = mapOf(
            0 to "PENDING", 1 to "RUNNING", 2 to "SUCCEEDED",
            3 to "FAILED", 4 to "CANCELED", 5 to "TIMEOUT", 6 to "PAUSED",
        )
        return RtdlNodeState(
            nodeIndex = state.nodeIndex,
            state = stateNames[state.state] ?: state.state.toString(),
            leafResult = if (state.hasLeafResult()) CapabilityCallResult(
                callId = state.leafResult.callId,
                providerId = state.leafResult.providerId,
                contractId = state.leafResult.contractId,
                name = state.leafResult.contractId.substringAfterLast("/"),
                success = state.leafResult.success,
                output = state.leafResult.output,
                error = state.leafResult.error,
            ) else null,
            planId = state.planId,
            opId = state.opId,
            description = state.description,
        )
    }
}
