package com.robonix.client.domain

import com.robonix.client.AppLog
import com.robonix.client.data.model.HandsfreeStatus
import com.robonix.client.data.model.VoiceEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Shared hands-free (免提) state — a @Singleton like RtdlStateHolder so the
 * Audio screen (toggle + status), ChatViewModel (incoming voice events) and
 * any other destination stay in sync across per-destination ViewModels.
 *
 * Mirrors the web client: poll /handsfree/status while the audio panel is
 * visible, toggle via /handsfree/set_enabled, and while enabled stream
 * /handsfree/events into the same chat pipeline as push-to-talk.
 */
@Singleton
class HandsfreeStateHolder @Inject constructor(
    private val chatRepository: ChatRepository,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val _status = MutableStateFlow<HandsfreeStatus?>(null)
    val status: StateFlow<HandsfreeStatus?> = _status.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    /** Mirror of ChatViewModel.voiceActive — handsfree toggling is blocked while a PTT session runs. */
    val voiceBusy = MutableStateFlow(false)

    private val _events = MutableSharedFlow<VoiceEvent>(extraBufferCapacity = 64)
    val events: SharedFlow<VoiceEvent> = _events.asSharedFlow()

    private var pollJob: Job? = null
    private var watchJob: Job? = null

    /** Start 3s status polling (call when the Audio screen becomes visible). */
    fun startPolling(target: String) {
        if (target.isBlank()) return
        if (pollJob?.isActive == true) return
        pollJob = scope.launch {
            while (isActive) {
                refreshOnce(target)
                delay(3000)
            }
        }
    }

    fun stopPolling() {
        pollJob?.cancel()
        pollJob = null
    }

    suspend fun refreshOnce(target: String) {
        if (target.isBlank()) return
        try {
            val s = chatRepository.getHandsfreeStatus(target)
            _status.value = s
            _error.value = null
            syncWatcher(target)
        } catch (e: Exception) {
            _error.value = e.message ?: "handsfree unavailable"
            AppLog.write("HANDSFREE", "status refresh failed: ${e.message}")
        }
    }

    /** Toggle hands-free; mic/speaker provider ids come from settings. */
    suspend fun setEnabled(
        target: String,
        enabled: Boolean,
        micProviderId: String,
        speakerProviderId: String,
    ): Boolean {
        _busy.value = true
        try {
            val result = chatRepository.setHandsfreeEnabled(target, enabled, micProviderId, speakerProviderId)
            result.fold(
                onSuccess = {
                    _status.value = it
                    _error.value = null
                },
                onFailure = { _error.value = it.message ?: "set handsfree failed" },
            )
            syncWatcher(target)
            return result.isSuccess
        } finally {
            _busy.value = false
        }
    }

    /**
     * Keep the events stream aligned with the enabled flag: watching while
     * enabled, cancelled when disabled. Restarting an already-active watcher
     * is a no-op.
     */
    private fun syncWatcher(target: String) {
        val enabled = _status.value?.enabled == true
        if (enabled && watchJob?.isActive != true && target.isNotBlank()) {
            watchJob = scope.launch {
                try {
                    chatRepository.watchHandsfreeEvents(target).collect { event ->
                        _events.tryEmit(event)
                    }
                } catch (e: Exception) {
                    AppLog.write("HANDSFREE", "events stream ended: ${e.message}")
                }
            }
        } else if (!enabled) {
            watchJob?.cancel()
            watchJob = null
        }
    }
}
