package com.robonix.client.domain

import android.util.Log
import com.robonix.client.data.grpc.AtlasClient
import com.robonix.client.data.grpc.LiaisonClient
import com.robonix.client.data.model.PilotEvent
import com.robonix.client.data.model.TimelineEvent
import kotlinx.coroutines.flow.Flow
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ChatRepository @Inject constructor(
    private val atlasClient: AtlasClient,
    private val liaisonClient: LiaisonClient,
) {
    companion object {
        // Contract IDs — must match the Robonix backend exactly
        const val CONTRACT_LIAISON_SUBMIT = "robonix/system/liaison/submit"
        const val CONTRACT_LIAISON_VOICE = "robonix/system/liaison/voice"
        const val CONTRACT_LIAISON_VOICE_FINISH = "robonix/system/liaison/voice/finish"
        const val CONTRACT_LIAISON_HANDSFREE_STATUS = "robonix/system/liaison/handsfree/status"
        const val CONTRACT_LIAISON_HANDSFREE_SET = "robonix/system/liaison/handsfree/set_enabled"
        const val CONTRACT_LIAISON_HANDSFREE_EVENTS = "robonix/system/liaison/handsfree/events"
        const val DEFAULT_LIAISON_PORT = 50081
    }

    /**
     * Discover Liaison's actual endpoint through Atlas, with a fallback to
     * {atlas_host}:50081 when discovery fails (matching the web client's
     * resolve_liaison behaviour).
     */
    private suspend fun resolveLiaisonEndpoint(
        atlasEndpoint: String,
        contractId: String = CONTRACT_LIAISON_SUBMIT,
    ): String {
        com.robonix.client.AppLog.write("GPRC", "resolveLiaison: atlas=$atlasEndpoint contract=$contractId")
        return try {
            val ep = atlasClient.discoverEndpoint(atlasEndpoint, contractId)
            com.robonix.client.AppLog.write("GPRC", "resolveLiaison: discovered $ep")
            ep
        } catch (e: Exception) {
            val host = atlasEndpoint.substringBeforeLast(":")
            val fallback = if (host.isNotBlank()) "$host:$DEFAULT_LIAISON_PORT"
            else throw RuntimeException("Cannot resolve Liaison endpoint from $atlasEndpoint")
            com.robonix.client.AppLog.write("GPRC", "resolveLiaison: discovery failed, fallback=$fallback", e)
            fallback
        }
    }

    suspend fun submitTextTask(
        atlasEndpoint: String,
        text: String,
        sessionId: String,
        userId: String,
        steer: Boolean = false,
        expectedTurnId: String = "",
    ): Flow<PilotEvent> {
        val liaisonEndpoint = resolveLiaisonEndpoint(atlasEndpoint, CONTRACT_LIAISON_SUBMIT)
        return liaisonClient.submitTask(
            target = liaisonEndpoint,
            text = text,
            sessionId = sessionId,
            userId = userId,
            steer = steer,
            expectedTurnId = expectedTurnId,
        )
    }

    suspend fun submitAbortTask(
        atlasEndpoint: String,
        sessionId: String,
        userId: String,
        expectedTurnId: String = "",
    ): Flow<PilotEvent> {
        // Abort sends through the same Liaison Submit contract, with abort_turn
        // set in the context JSON (matching web client's build_abort_task).
        val liaisonEndpoint = resolveLiaisonEndpoint(atlasEndpoint, CONTRACT_LIAISON_SUBMIT)
        return liaisonClient.submitTask(
            target = liaisonEndpoint,
            text = "",
            sessionId = sessionId,
            userId = userId,
            steer = false,
            expectedTurnId = expectedTurnId,
            isAbort = true,
        )
    }

    suspend fun startVoiceSession(
        atlasEndpoint: String,
        sessionId: String,
        userId: String,
        settings: com.robonix.client.data.model.ClientSettings,
        steer: Boolean = false,
        expectedTurnId: String = "",
    ): Flow<com.robonix.client.data.model.VoiceEvent> {
        val liaisonEndpoint = resolveLiaisonEndpoint(atlasEndpoint, CONTRACT_LIAISON_VOICE)
        return liaisonClient.startVoiceSession(
            target = liaisonEndpoint,
            sessionId = sessionId,
            userId = userId,
            recordSeconds = settings.recordSeconds,
            language = settings.language,
            micNodeId = settings.micNodeId,
            asrNodeId = settings.asrNodeId,
            voiceprintNodeId = settings.voiceprintNodeId,
            ttsNodeId = settings.ttsNodeId,
            speakerNodeId = settings.speakerNodeId,
            steer = steer,
            expectedTurnId = expectedTurnId,
        )
    }

    /**
     * Ask Liaison to stop recording early and submit whatever has been
     * recognized so far (web client's finish_voice_capture). The voice
     * stream itself stays open and later delivers asr_final/session_done.
     */
    suspend fun finishVoiceCapture(
        atlasEndpoint: String,
        sessionId: String,
    ): Result<String> {
        val liaisonEndpoint = resolveLiaisonEndpoint(atlasEndpoint, CONTRACT_LIAISON_VOICE_FINISH)
        return liaisonClient.finishVoiceCapture(target = liaisonEndpoint, sessionId = sessionId)
    }

    // ---- Handsfree (免提模式) ----

    suspend fun getHandsfreeStatus(atlasEndpoint: String): com.robonix.client.data.model.HandsfreeStatus {
        val ep = resolveLiaisonEndpoint(atlasEndpoint, CONTRACT_LIAISON_HANDSFREE_STATUS)
        return liaisonClient.getHandsfreeStatus(ep)
    }

    suspend fun setHandsfreeEnabled(
        atlasEndpoint: String,
        enabled: Boolean,
        micProviderId: String = "",
        speakerProviderId: String = "",
    ): Result<com.robonix.client.data.model.HandsfreeStatus> {
        val ep = resolveLiaisonEndpoint(atlasEndpoint, CONTRACT_LIAISON_HANDSFREE_SET)
        return liaisonClient.setHandsfreeEnabled(ep, enabled, micProviderId, speakerProviderId)
    }

    suspend fun watchHandsfreeEvents(atlasEndpoint: String): Flow<com.robonix.client.data.model.VoiceEvent> {
        val ep = resolveLiaisonEndpoint(atlasEndpoint, CONTRACT_LIAISON_HANDSFREE_EVENTS)
        return liaisonClient.watchHandsfreeEvents(ep)
    }

    fun generateMessageId(): String = UUID.randomUUID().toString()
}

fun createTimelineEvent(kind: String, text: String): TimelineEvent =
    TimelineEvent(kind, text, java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault()).format(java.util.Date()))
