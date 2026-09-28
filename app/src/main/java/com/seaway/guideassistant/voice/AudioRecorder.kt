package com.seaway.guideassistant.voice

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper

/**
 * 采集16kHz单通道16bit PCM音频，读取线程运行在独立Thread上，通过Handler回调到主线程
 */
class AudioRecorder(private val onPcmChunk: (ByteArray) -> Unit, private val onError: () -> Unit) {

    companion object {
        private const val SAMPLE_RATE = 16000
        private const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        private const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private var audioRecord: AudioRecord? = null
    private var readThread: Thread? = null
    @Volatile
    private var recording = false

    fun start(): Boolean {
        val minBufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        if (minBufferSize <= 0) return false
        val bufferSize = minBufferSize * 2
        val record = try {
            AudioRecord(MediaRecorder.AudioSource.MIC, SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT, bufferSize)
        } catch (e: SecurityException) {
            return false
        }
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            return false
        }
        audioRecord = record
        recording = true
        record.startRecording()
        readThread = Thread {
            val buffer = ByteArray(bufferSize)
            while (recording) {
                val readCount = record.read(buffer, 0, buffer.size)
                if (readCount > 0) {
                    val chunk = buffer.copyOf(readCount)
                    mainHandler.post { if (recording) onPcmChunk(chunk) }
                } else if (readCount < 0) {
                    mainHandler.post { onError() }
                    break
                }
            }
        }
        readThread?.start()
        return true
    }

    fun stop() {
        recording = false
        readThread?.join(500)
        readThread = null
        audioRecord?.let {
            try {
                it.stop()
            } catch (e: IllegalStateException) {
                //录音尚未开始即被停止，忽略
            }
            it.release()
        }
        audioRecord = null
    }
}
