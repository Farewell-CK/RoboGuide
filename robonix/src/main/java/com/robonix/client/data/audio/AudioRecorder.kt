package com.robonix.client.data.audio

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.withContext
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AudioRecorder @Inject constructor(
    private val context: Context,
) {
    private val sampleRate = 16000
    private val channelConfig = AudioFormat.CHANNEL_IN_MONO
    private val audioFormat = AudioFormat.ENCODING_PCM_16BIT
    private var audioRecord: AudioRecord? = null
    private var isRecording = false

    fun hasPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    val bufferSize: Int by lazy {
        AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat) * 2
    }

    fun startRecording(): Flow<ByteArray> = callbackFlow {
        if (isRecording) {
            close(IllegalStateException("Already recording"))
            return@callbackFlow
        }

        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            sampleRate,
            channelConfig,
            audioFormat,
            bufferSize,
        ).also { recorder ->
            if (recorder.state != AudioRecord.STATE_INITIALIZED) {
                close(RuntimeException("AudioRecord init failed"))
                return@callbackFlow
            }
            recorder.startRecording()
            isRecording = true
        }

        val buffer = ByteArray(bufferSize)
        try {
            while (isRecording) {
                val bytesRead = audioRecord?.read(buffer, 0, buffer.size) ?: -1
                if (bytesRead > 0) {
                    trySend(buffer.copyOf(bytesRead))
                }
            }
        } finally {
            stopInternal()
            close()
        }

        awaitClose { stopInternal() }
    }

    fun stop() {
        stopInternal()
    }

    private fun stopInternal() {
        isRecording = false
        audioRecord?.apply {
            if (state == AudioRecord.STATE_INITIALIZED && recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                stop()
            }
            release()
        }
        audioRecord = null
    }

    suspend fun recordForDuration(seconds: Float): ByteArray = withContext(Dispatchers.IO) {
        if (!hasPermission()) throw SecurityException("RECORD_AUDIO permission not granted")

        val totalBytes = (sampleRate * seconds * 2).toInt() // 16-bit mono
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            sampleRate,
            channelConfig,
            audioFormat,
            maxOf(bufferSize, totalBytes),
        )
        try {
            if (recorder.state != AudioRecord.STATE_INITIALIZED) {
                throw RuntimeException("AudioRecord init failed")
            }
            recorder.startRecording()
            val data = ByteArray(totalBytes)
            var offset = 0
            val deadline = System.currentTimeMillis() + (seconds * 1000).toLong()
            while (offset < totalBytes && System.currentTimeMillis() < deadline) {
                val read = recorder.read(data, offset, totalBytes - offset)
                if (read > 0) offset += read
            }
            data.copyOf(offset)
        } finally {
            recorder.release()
        }
    }
}
