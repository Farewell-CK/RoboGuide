package com.elabrador.mobilenavigation;

import android.annotation.SuppressLint;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okio.ByteString;

/** Foreground voice command input plus short navigation TTS prompts. */
final class VoiceNavigationController implements AutoCloseable {
    private static final String TAG = "VoiceNavigation";
    interface Listener {
        void onStatus(String status);
        void onCommand(VoiceCommand command, String recognizedText);
    }

    private static final String ASR_URL = "wss://aitoys.seawayos.com/asr/v1/";
    private static final String TTS_URL = "http://183.242.33.186:8811/v1/audio/speech";
    private static final String TTS_API_KEY = BuildConfig.TTS_API_KEY;
    private static final int SAMPLE_RATE = 16_000;
    private static final int CHUNK_SAMPLES = 1_600; // 100 ms
    private static final int MIN_START_RMS = 350;
    private static final int MIN_END_RMS = 280;
    private static final int CALIBRATION_CHUNKS = 10;
    private static final int START_CHUNKS = 2;
    private static final int END_SILENCE_CHUNKS = 6;
    private static final int MAX_UTTERANCE_CHUNKS = 60;
    private static final int ASR_TIMEOUT_SECONDS = 12;

    private final Listener listener;
    private final OkHttpClient http = new OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build();
    private final ExecutorService recorderExecutor = Executors.newSingleThreadExecutor(r ->
            new Thread(r, "voice-recorder"));
    private final ExecutorService networkExecutor = Executors.newSingleThreadExecutor(r ->
            new Thread(r, "voice-network"));
    private final AtomicBoolean recording = new AtomicBoolean(false);
    private final AtomicBoolean asrPending = new AtomicBoolean(false);
    private final AtomicBoolean ttsSpeaking = new AtomicBoolean(false);
    private final ConcurrentLinkedQueue<String> pendingPrompts = new ConcurrentLinkedQueue<>();
    private final AtomicReference<String> pendingGuidance = new AtomicReference<>();
    private final AtomicBoolean promptDrainPending = new AtomicBoolean(false);
    private volatile String asrToken = "";
    private volatile boolean enabled;
    private volatile boolean closed;
    private volatile AudioRecord activeRecorder;
    private String lastGuidance = "";
    private long lastGuidanceNanos;

    VoiceNavigationController(Listener listener) {
        this.listener = listener;
    }

    void setAsrToken(String token) {
        asrToken = token == null ? "" : token.trim();
    }

    boolean isEnabled() {
        return enabled;
    }

    void setEnabled(boolean value) {
        enabled = value;
        if (value) resume();
        else {
            pause();
            listener.onStatus("语音控制已关闭");
        }
    }

    void resume() {
        if (!enabled || closed || !recording.compareAndSet(false, true)) return;
        recorderExecutor.execute(this::recordLoop);
    }

    void pause() {
        recording.set(false);
        AudioRecord recorder = activeRecorder;
        if (recorder != null) {
            try { recorder.stop(); } catch (Exception ignored) {}
        }
    }

    void announceGuidance(String screenGuidance) {
        if (!enabled || screenGuidance == null) return;
        String prompt = screenGuidance.trim();
        if (!("停止".equals(prompt) || "左转".equals(prompt) || "右转".equals(prompt)
                || "向后转".equals(prompt) || "直走".equals(prompt))) return;
        long now = System.nanoTime();
        long repeatNanos = TimeUnit.SECONDS.toNanos("停止".equals(prompt) ? 2 : 5);
        if (prompt.equals(lastGuidance) && now - lastGuidanceNanos < repeatNanos) return;
        lastGuidance = prompt;
        lastGuidanceNanos = now;
        // Keep only the newest direction. A stale queued turn is unsafe after the
        // planner has already selected a different direction.
        pendingGuidance.set(prompt);
        drainPrompts();
    }

    void speak(String prompt) {
        if (closed || prompt == null || prompt.trim().isEmpty()) return;
        pendingPrompts.offer(prompt.trim());
        drainPrompts();
    }

    void speakCritical(String prompt) {
        pendingPrompts.clear();
        pendingGuidance.set(null);
        resetGuidance();
        speak(prompt);
    }

    void resetGuidance() {
        lastGuidance = "";
        lastGuidanceNanos = 0L;
    }

    private void drainPrompts() {
        if (!promptDrainPending.compareAndSet(false, true)) return;
        networkExecutor.execute(() -> {
            try {
                String next;
                while (!closed) {
                    next = pendingPrompts.poll();
                    if (next == null) next = pendingGuidance.getAndSet(null);
                    if (next == null) break;
                    synthesizeAndPlay(next);
                }
            } finally {
                promptDrainPending.set(false);
                if (!closed && (!pendingPrompts.isEmpty() || pendingGuidance.get() != null)) {
                    drainPrompts();
                }
            }
        });
    }

    @SuppressLint("MissingPermission")
    private void recordLoop() {
        int minimum = AudioRecord.getMinBufferSize(SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        int bufferBytes = Math.max(CHUNK_SAMPLES * 4, minimum);
        AudioRecord recorder = null;
        try {
            recorder = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, bufferBytes);
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new IllegalStateException("麦克风初始化失败");
            }
            recorder.startRecording();
            activeRecorder = recorder;
            listener.onStatus("正在聆听：可说目的地或“开始导航”");
            byte[] chunk = new byte[CHUNK_SAMPLES * 2];
            ArrayDeque<byte[]> preRoll = new ArrayDeque<>();
            ByteArrayOutputStream utterance = null;
            int voicedRun = 0, silenceRun = 0, chunks = 0;
            int calibrationLeft = CALIBRATION_CHUNKS;
            double noiseRms = 200.0;
            while (recording.get() && !closed) {
                int read = recorder.read(chunk, 0, chunk.length);
                if (read <= 0) continue;
                if (ttsSpeaking.get() || asrPending.get()) {
                    preRoll.clear(); utterance = null;
                    voicedRun = silenceRun = chunks = 0;
                    continue;
                }
                byte[] current = Arrays.copyOf(chunk, read);
                int level = rms(current);
                if (utterance == null) {
                    preRoll.addLast(current);
                    while (preRoll.size() > 4) preRoll.removeFirst();
                    if (calibrationLeft > 0) {
                        noiseRms = calibrationLeft == CALIBRATION_CHUNKS
                                ? level : noiseRms * 0.8 + level * 0.2;
                        calibrationLeft--;
                        voicedRun = 0;
                        continue;
                    }
                    int startThreshold = Math.max(MIN_START_RMS,
                            (int) (noiseRms * 2.2));
                    boolean voiced = level >= startThreshold;
                    voicedRun = voiced ? voicedRun + 1 : 0;
                    if (!voiced) noiseRms = noiseRms * 0.95 + level * 0.05;
                    if (voicedRun >= START_CHUNKS) {
                        utterance = new ByteArrayOutputStream();
                        for (byte[] prior : preRoll) utterance.write(prior, 0, prior.length);
                        preRoll.clear();
                        chunks = voicedRun;
                        silenceRun = 0;
                        Log.i(TAG, "speech start rms=" + level + " noise="
                                + (int) noiseRms + " threshold=" + startThreshold);
                        listener.onStatus("听到语音，正在录入…");
                    }
                    continue;
                }
                utterance.write(current, 0, current.length);
                chunks++;
                int endThreshold = Math.max(MIN_END_RMS, (int) (noiseRms * 1.5));
                silenceRun = level >= endThreshold ? 0 : silenceRun + 1;
                if (silenceRun >= END_SILENCE_CHUNKS || chunks >= MAX_UTTERANCE_CHUNKS) {
                    byte[] pcm = utterance.toByteArray();
                    Log.i(TAG, "speech end chunks=" + chunks + " silence="
                            + silenceRun + " bytes=" + pcm.length);
                    utterance = null; voicedRun = silenceRun = chunks = 0;
                    submitAsr(pcm);
                }
            }
        } catch (Exception error) {
            if (recording.get() && !closed) listener.onStatus("语音输入错误：" + error.getMessage());
        } finally {
            recording.set(false);
            if (activeRecorder == recorder) activeRecorder = null;
            if (recorder != null) {
                try { recorder.stop(); } catch (Exception ignored) {}
                recorder.release();
            }
        }
    }

    private void submitAsr(byte[] pcm) {
        if (!asrPending.compareAndSet(false, true)) return;
        Log.i(TAG, "submit ASR bytes=" + pcm.length);
        listener.onStatus("正在识别…");
        networkExecutor.execute(() -> {
            try {
                String text = transcribe(pcm);
                Log.i(TAG, "ASR result=" + text);
                if (text.isEmpty()) listener.onStatus("没有识别到有效语音");
                else {
                    listener.onStatus("识别结果：" + text);
                    listener.onCommand(VoiceCommand.parse(text), text);
                }
            } catch (Exception error) {
                Log.e(TAG, "ASR failed", error);
                listener.onStatus("ASR 失败：" + error.getMessage());
            } finally {
                asrPending.set(false);
            }
        });
    }

    private String transcribe(byte[] pcm) throws Exception {
        Exception first = null;
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                return transcribeOnce(pcm, attempt == 0);
            } catch (Exception error) {
                first = error;
                Log.w(TAG, "ASR attempt " + (attempt + 1) + " failed: " + error.getMessage());
            }
        }
        throw first == null ? new IllegalStateException("ASR 失败") : first;
    }

    private String transcribeOnce(byte[] pcm, boolean documentedProtocol) throws Exception {
        Request.Builder builder = new Request.Builder().url(ASR_URL);
        String token = asrToken;
        if (!token.isEmpty()) builder.header("Authorization", "Bearer " + token);
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        AtomicReference<Throwable> failure = new AtomicReference<>();
        WebSocket socket = http.newWebSocket(builder.build(), new WebSocketListener() {
            @Override public void onOpen(WebSocket webSocket, Response response) {
                Log.i(TAG, "ASR WebSocket open HTTP " + response.code());
                String init = documentedProtocol
                        ? "{\"is_speaking\":true,\"language\":\"zh\"}"
                        : "{\"mode\":\"offline\",\"wav_name\":\"mobile-navigation.pcm\","
                        + "\"wav_format\":\"pcm\",\"is_speaking\":true,"
                        + "\"language\":\"zh\",\"hotwords\":\"开始导航 停止导航 导航到 左转 右转 直走\"}";
                webSocket.send(init);
                webSocket.send(ByteString.of(pcm));
                webSocket.send("{\"is_speaking\":false}");
            }
            @Override public void onMessage(WebSocket webSocket, String message) {
                Log.d(TAG, "ASR message=" + message);
                try {
                    JSONObject json = new JSONObject(message);
                    String text = json.optString("text", "").trim();
                    if (!text.isEmpty()) result.set(text);
                    // The documented service sends is_final=true. Accept a
                    // text-only response as final too for older workers.
                    if (json.optBoolean("is_final", !text.isEmpty())) {
                        done.countDown();
                        webSocket.close(1000, "done");
                    }
                } catch (Exception error) {
                    failure.set(error); done.countDown();
                }
            }
            @Override public void onFailure(WebSocket webSocket, Throwable error,
                                            Response response) {
                Log.e(TAG, "ASR WebSocket failure HTTP "
                        + (response == null ? "none" : response.code()), error);
                failure.set(error); done.countDown();
            }
            @Override public void onClosing(WebSocket webSocket, int code, String reason) {
                Log.i(TAG, "ASR WebSocket closing code=" + code + " reason=" + reason);
            }
            @Override public void onClosed(WebSocket webSocket, int code, String reason) {
                Log.i(TAG, "ASR WebSocket closed code=" + code + " reason=" + reason);
                if (result.get().isEmpty() && failure.get() == null) {
                    failure.set(new IllegalStateException("服务端关闭连接(" + code + ")"));
                    done.countDown();
                }
            }
        });
        if (!done.await(ASR_TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
            socket.cancel();
            throw new IllegalStateException("识别超时");
        }
        if (failure.get() != null) throw new IllegalStateException(failure.get().getMessage());
        return result.get();
    }

    private void synthesizeAndPlay(String prompt) {
        ttsSpeaking.set(true);
        listener.onStatus("正在播报：" + prompt);
        try {
            JSONObject json = new JSONObject();
            json.put("model", "cosyvoice3-2512");
            json.put("voice", "wangyi");
            json.put("input", prompt);
            json.put("response_format", "wav");
            json.put("speed", 1.0);
            Request request = new Request.Builder().url(TTS_URL)
                    .header("Authorization", "Bearer " + TTS_API_KEY)
                    .post(RequestBody.create(json.toString().getBytes(StandardCharsets.UTF_8),
                            MediaType.get("application/json; charset=utf-8")))
                    .build();
            try (Response response = http.newCall(request).execute()) {
                if (!response.isSuccessful() || response.body() == null) {
                    throw new IllegalStateException("TTS HTTP " + response.code());
                }
                playWav(response.body().bytes());
            }
            listener.onStatus("正在聆听：可说目的地或“开始导航”");
        } catch (Exception error) {
            listener.onStatus("TTS 失败：" + error.getMessage());
        } finally {
            try { Thread.sleep(300); } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            ttsSpeaking.set(false);
        }
    }

    private static void playWav(byte[] wav) {
        WavInfo info = WavInfo.parse(wav);
        int channel = info.channels == 1 ? AudioFormat.CHANNEL_OUT_MONO
                : AudioFormat.CHANNEL_OUT_STEREO;
        AudioTrack track = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                .setAudioFormat(new AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(info.sampleRate).setChannelMask(channel).build())
                .setBufferSizeInBytes(info.dataLength)
                .setTransferMode(AudioTrack.MODE_STATIC).build();
        try {
            track.write(wav, info.dataOffset, info.dataLength);
            track.play();
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
            while (track.getPlaybackHeadPosition() < info.frames
                    && System.nanoTime() < deadline) {
                try { Thread.sleep(20); } catch (InterruptedException error) {
                    Thread.currentThread().interrupt(); break;
                }
            }
        } finally {
            track.stop();
            track.release();
        }
    }

    private static int rms(byte[] pcm) {
        long squares = 0;
        int samples = pcm.length / 2;
        for (int i = 0; i + 1 < pcm.length; i += 2) {
            int value = (short) ((pcm[i] & 0xff) | (pcm[i + 1] << 8));
            squares += (long) value * value;
        }
        return samples == 0 ? 0 : (int) Math.sqrt(squares / (double) samples);
    }

    @Override public void close() {
        closed = true;
        enabled = false;
        pause();
        recorderExecutor.shutdownNow();
        networkExecutor.shutdownNow();
        http.dispatcher().executorService().shutdownNow();
        http.connectionPool().evictAll();
    }

    private static final class WavInfo {
        final int sampleRate, channels, dataOffset, dataLength, frames;
        WavInfo(int rate, int channels, int offset, int length) {
            sampleRate = rate; this.channels = channels;
            dataOffset = offset; dataLength = length;
            frames = length / (channels * 2);
        }
        static WavInfo parse(byte[] wav) {
            if (wav.length < 44 || !ascii(wav, 0, "RIFF") || !ascii(wav, 8, "WAVE")) {
                throw new IllegalArgumentException("TTS 返回的 WAV 无效");
            }
            int rate = 0, channels = 0, bits = 0, offset = -1, length = 0;
            int p = 12;
            while (p + 8 <= wav.length) {
                int size = littleInt(wav, p + 4);
                int body = p + 8;
                if (size < 0 || body + size > wav.length) break;
                if (ascii(wav, p, "fmt ") && size >= 16) {
                    channels = littleShort(wav, body + 2);
                    rate = littleInt(wav, body + 4);
                    bits = littleShort(wav, body + 14);
                } else if (ascii(wav, p, "data")) {
                    offset = body; length = size; break;
                }
                p = body + size + (size & 1);
            }
            if (rate <= 0 || (channels != 1 && channels != 2) || bits != 16
                    || offset < 0 || length <= 0) {
                throw new IllegalArgumentException("TTS WAV 格式不受支持");
            }
            return new WavInfo(rate, channels, offset, length);
        }
        private static boolean ascii(byte[] data, int offset, String text) {
            if (offset + text.length() > data.length) return false;
            for (int i = 0; i < text.length(); i++) {
                if (data[offset + i] != (byte) text.charAt(i)) return false;
            }
            return true;
        }
        private static int littleShort(byte[] data, int offset) {
            return (data[offset] & 0xff) | ((data[offset + 1] & 0xff) << 8);
        }
        private static int littleInt(byte[] data, int offset) {
            return littleShort(data, offset) | (littleShort(data, offset + 2) << 16);
        }
    }
}
