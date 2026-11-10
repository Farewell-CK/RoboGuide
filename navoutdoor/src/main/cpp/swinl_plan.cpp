// Stateful application adapter for the validated build25 MDLA runtime.
#include "mdla_runtime.inc"
#include <sys/stat.h>
#include <arm_neon.h>

namespace {
struct DirectBuffer { void* address; jlong capacity; };

struct Runner {
    std::vector<BufferDesc> bufferDescs;
    std::vector<SharedBuffer> buffers;
    std::vector<bool> externalInput;
    std::vector<Subgraph> subgraphs;
    std::vector<uint32_t> resultBuffers, lastUse;
    std::vector<std::string> keys, readyPaths;
    std::vector<bool> compiled;
    std::string cacheDir;
    RuntimeOptions options{ANEURALNETWORKS_PREFER_FAST_SINGLE_ANSWER, false, true, true, "mtk-mdla_shim", false, false};
    const ANeuralNetworksDevice* device = nullptr;
    TemplateCache templates{16};

    Runner(const std::string& path, const std::string& cache,
           int preference = ANEURALNETWORKS_PREFER_FAST_SINGLE_ANSWER,
           bool highPriority = false, bool measureTiming = false, bool burst = true) : cacheDir(cache) {
        Reader reader(path);
        if (reader.get<uint32_t>() != 0x4e414c50u) throw std::runtime_error("plan magic");
        uint32_t version = reader.get<uint32_t>();
        if (version != 2) throw std::runtime_error("Swin-L requires plan v2");
#include "swinl_parser.inc"
        if (resultBuffers.size() != 2 || buffers.empty()) throw std::runtime_error("Swin-L result contract");
        const auto& inputShape = bufferDescs[0].dims;
        if (inputShape.size() != 4 || inputShape[0] != 1 || inputShape[1] != 3 ||
            inputShape[2] == 0 || inputShape[3] == 0 || inputShape[2] > 480 || inputShape[3] > 640)
            throw std::runtime_error("Swin-L input shape");
        const size_t pixels = static_cast<size_t>(inputShape[2]) * inputShape[3];
        if (bufferDescs[0].type != ANEURALNETWORKS_TENSOR_FLOAT16 ||
            bufferDescs[resultBuffers[0]].type != ANEURALNETWORKS_TENSOR_INT32 ||
            bufferDescs[resultBuffers[1]].type != ANEURALNETWORKS_TENSOR_FLOAT16 ||
            byte_size(bufferDescs[0].type, inputShape) != pixels * 6 ||
            byte_size(bufferDescs[resultBuffers[0]].type, bufferDescs[resultBuffers[0]].dims) != pixels * 4 ||
            byte_size(bufferDescs[resultBuffers[1]].type, bufferDescs[resultBuffers[1]].dims) != pixels * 2)
            throw std::runtime_error("Swin-L tensor sizes");
        mkdir(cacheDir.c_str(), 0700);
        if (preference < ANEURALNETWORKS_PREFER_LOW_POWER ||
            preference > ANEURALNETWORKS_PREFER_SUSTAINED_SPEED)
            throw std::runtime_error("invalid NNAPI preference");
        options.preference = preference;
        options.highPriority = highPriority;
        options.measureTiming = measureTiming;
        options.burst = burst;
        device = accelerator_device(options.target);
        externalInput[0] = true;
        lastUse.assign(buffers.size(), 0);
        for (uint32_t s = 0; s < subgraphs.size(); s++) {
            for (uint32_t v : subgraphs[s].inputs) lastUse.at(subgraphs[s].values[v].binding) = s;
            for (uint32_t v : subgraphs[s].outputs) lastUse.at(subgraphs[s].values[v].binding) = s;
        }
        for (auto id : resultBuffers) lastUse[id] = subgraphs.size();
        for (uint32_t id = 0; id < buffers.size(); id++) if (externalInput[id]) lastUse[id] = subgraphs.size();

        // Same cost policy and 16-slot limit as verified170. Retain selected
        // templates across frames without retaining all learned models.
        keys.resize(subgraphs.size());
        std::unordered_map<std::string, uint32_t> frequency;
        for (uint32_t s = 0; s < subgraphs.size(); s++) {
            keys[s] = template_key(subgraphs[s]);
            if (!keys[s].empty()) frequency[keys[s]]++;
        }
        std::unordered_map<std::string, uint64_t> score;
        for (uint32_t s = 0; s < subgraphs.size(); s++) if (!keys[s].empty())
            score[keys[s]] = (subgraphs[s].ops.size() + 8) * static_cast<uint64_t>(frequency[keys[s]]);
        std::vector<std::pair<uint64_t, std::string>> ranked;
        for (const auto& e : score) ranked.emplace_back(e.second, e.first);
        std::sort(ranked.begin(), ranked.end(), [](const auto& a, const auto& b) {
            return a.first != b.first ? a.first > b.first : a.second < b.second;
        });
        std::unordered_map<std::string, bool> selected;
        for (size_t i = 0; i < ranked.size() && i < 16; i++) selected[ranked[i].second] = true;
        for (auto& key : keys) if (!key.empty() && !selected.count(key)) key.clear();
        readyPaths.resize(subgraphs.size());
        compiled.assign(subgraphs.size(), false);
        for (uint32_t s = 0; s < subgraphs.size(); s++) {
            uint8_t signature[32]; cache_token(subgraphs[s], signature, options);
            static const char hex[] = "0123456789abcdef";
            readyPaths[s] = cacheDir + "/ready-v2-";
            for (auto b : signature) { readyPaths[s] += hex[b >> 4]; readyPaths[s] += hex[b & 15]; }
            compiled[s] = static_cast<bool>(std::ifstream(readyPaths[s]));
        }
        __android_log_print(ANDROID_LOG_INFO, "SwinLPlan", "loaded subgraphs=%zu cache_slots=16 prefetch=true burst=%d measure_timing=%d cpu_fallback=false", subgraphs.size(), options.burst, options.measureTiming);
    }

    double run(const DirectBuffer& input, const DirectBuffer& mask, const DirectBuffer& confidence, int64_t timing[5]) {
        auto start = std::chrono::steady_clock::now();
        size_t inputBytes = byte_size(bufferDescs[0].type, bufferDescs[0].dims);
        if (!input.address || input.capacity != inputBytes) throw std::runtime_error("input direct buffer size");
        buffers[0].resize(inputBytes);
        std::memcpy(buffers[0].data(), input.address, inputBytes);
        double compileMs = 0, computeMs = 0;
        uint32_t cacheHits = 0, coldCompiles = 0;
        struct Prepared { uint32_t sub; std::unique_ptr<ModelHolder> holder; SubgraphResult result; };
        std::future<Prepared> pending;
        for (uint32_t s = 0; s < subgraphs.size(); s++) {
            bool wasCompiled = compiled[s];
            if (!wasCompiled) { templates.clear(); coldCompiles++; }
            auto* resident = wasCompiled ? templates.slot(keys[s]) : nullptr;
            std::unique_ptr<ModelHolder> temporary;
            SubgraphResult preparedResult;
            bool fromPrefetch = pending.valid();
            if (fromPrefetch) {
                Prepared prepared = pending.get();
                if (prepared.sub != s) throw std::runtime_error("prefetch order mismatch");
                preparedResult = prepared.result;
                if (!resident) resident = &temporary;
                *resident = std::move(prepared.holder);
            }
            auto prefetchNext = [&]() {
                uint32_t next = s + 1;
                if (next >= subgraphs.size() || !wasCompiled || !compiled[next] ||
                    templates.ready(keys[next]) || subgraphs[next].ops.size() > 400) return;
                pending = std::async(std::launch::async, [&, next]() {
                    Prepared prepared; prepared.sub = next;
                    prepared.result = execute_subgraph(subgraphs[next], device, buffers, next, false, true,
                        cacheDir, false, options, &prepared.holder, {}, true);
                    return prepared;
                });
            };
            SubgraphResult result;
            if (fromPrefetch && !preparedResult.ok) result = preparedResult;
            else {
                result = execute_subgraph(subgraphs[s], device, buffers, s, false, false,
                    cacheDir, false, options, resident, prefetchNext);
                if (fromPrefetch) { result.reused = false; result.compileMs += preparedResult.compileMs; }
            }
            if (!result.ok || result.accepted != subgraphs[s].ops.size()) {
                // Wait for the single preparation task before releasing models.
                if (pending.valid()) pending.get();
                templates.clear();
                throw std::runtime_error("sub=" + std::to_string(s) + " " + result.error);
            }
            if (!wasCompiled) { std::ofstream marker(readyPaths[s]); marker << "compiled\n"; compiled[s] = true; }
            if (result.reused) cacheHits++;
            compileMs += result.compileMs;
            computeMs += result.computeMs;
            for (uint32_t id = 0; id < buffers.size(); id++)
                if (!externalInput[id] && lastUse[id] == s) buffers[id].reset();
            if (!wasCompiled || s % 20 == 0)
                __android_log_print(ANDROID_LOG_INFO, "SwinLPlan", "sub=%u/%zu compile_ms=%.1f compute_ms=%.1f", s + 1, subgraphs.size(), result.compileMs, result.computeMs);
        }
        const DirectBuffer outputs[] = {mask, confidence};
        for (int i = 0; i < 2; i++) {
            const auto& buffer = buffers[resultBuffers[i]];
            if (!outputs[i].address || outputs[i].capacity != buffer.size() || buffer.empty()) throw std::runtime_error("output direct buffer size");
            std::memcpy(outputs[i].address, buffer.data(), buffer.size());
        }
        double total = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
        timing[0] = compileMs * 1000000; timing[1] = computeMs * 1000000; timing[2] = total * 1000000;
        timing[3] = cacheHits; timing[4] = coldCompiles;
        __android_log_print(ANDROID_LOG_INFO, "SwinLPlan", "complete ms=%.3f cache_hits=%u cold_compiles=%u cpu_fallback=false", total, cacheHits, coldCompiles);
        return total;
    }
};

std::string utf8(JNIEnv* env, jstring text) {
    const char* chars = env->GetStringUTFChars(text, nullptr);
    if (!chars) throw std::runtime_error("JNI string");
    std::string result(chars); env->ReleaseStringUTFChars(text, chars); return result;
}
} // namespace

extern "C" JNIEXPORT jlong JNICALL Java_com_elabrador_mobilenavigation_NativeSwinlPlan_nativeCreate(JNIEnv* env, jclass, jstring plan, jstring cache) {
    try { return reinterpret_cast<jlong>(new Runner(utf8(env, plan), utf8(env, cache))); }
    catch (const std::exception& e) { __android_log_print(ANDROID_LOG_ERROR, "SwinLPlan", "create failed: %s", e.what()); return 0; }
}
extern "C" JNIEXPORT jlong JNICALL Java_com_elabrador_mobilenavigation_NativeSwinlPlan_nativeCreateTuned(JNIEnv* env, jclass, jstring plan, jstring cache, jint preference, jboolean highPriority, jboolean measureTiming, jboolean burst) {
    try { return reinterpret_cast<jlong>(new Runner(utf8(env, plan), utf8(env, cache), preference, highPriority, measureTiming, burst)); }
    catch (const std::exception& e) { __android_log_print(ANDROID_LOG_ERROR, "SwinLPlan", "tuned create failed: %s", e.what()); return 0; }
}
extern "C" JNIEXPORT jlong JNICALL Java_com_elabrador_mobilenavigation_NativeSwinlPlan_nativeRun(JNIEnv* env, jclass, jlong handle, jobject input, jobject mask, jobject confidence, jlongArray timings) {
    if (!handle) return -1;
    try {
        auto direct = [&](jobject b) { return DirectBuffer{env->GetDirectBufferAddress(b), env->GetDirectBufferCapacity(b)}; };
        int64_t values[5]{};
        double total = reinterpret_cast<Runner*>(handle)->run(direct(input), direct(mask), direct(confidence), values);
        if (timings) { jlong out[5]; for (int i = 0; i < 5; i++) out[i] = values[i]; env->SetLongArrayRegion(timings, 0, std::min<jsize>(5, env->GetArrayLength(timings)), out); }
        return static_cast<jlong>(total);
    } catch (const std::exception& e) { __android_log_print(ANDROID_LOG_ERROR, "SwinLPlan", "run failed: %s", e.what()); return -1; }
}
extern "C" JNIEXPORT void JNICALL Java_com_elabrador_mobilenavigation_NativeSwinlPlan_nativeDestroy(JNIEnv*, jclass, jlong handle) { delete reinterpret_cast<Runner*>(handle); }

// RGB8 values are exactly representable in binary16. Keep resize semantics
// identical to the Java path; vectorize the common 640x480 camera input.
extern "C" JNIEXPORT void JNICALL Java_com_elabrador_mobilenavigation_NativeSwinlPlan_nativePrepareRgb(
        JNIEnv* env, jclass, jbyteArray rgb, jint stride, jint xOffset, jint yOffset,
        jint cropWidth, jint cropHeight, jint outputWidth, jint outputHeight, jobject output) {
    auto* target = static_cast<float16_t*>(env->GetDirectBufferAddress(output));
    const size_t plane = static_cast<size_t>(outputWidth) * outputHeight;
    if (outputWidth <= 0 || outputHeight <= 0 || outputWidth > 640 || outputHeight > 480 ||
            !target || env->GetDirectBufferCapacity(output) != plane * 6) {
        env->ThrowNew(env->FindClass("java/lang/IllegalArgumentException"), "RGB output buffer"); return;
    }
    jbyte* source = env->GetByteArrayElements(rgb, nullptr);
    if (!source) return;
    const auto* pixels = reinterpret_cast<const uint8_t*>(source);
    if (cropWidth == outputWidth && cropHeight == outputHeight && outputWidth % 8 == 0) {
        for (int y = 0; y < outputHeight; y++) for (int x = 0; x < outputWidth; x += 8) {
            auto channels = vld3_u8(pixels + (yOffset + y) * stride + (xOffset + x) * 3);
            for (int c = 0; c < 3; c++) {
                uint16x8_t v = vmovl_u8(channels.val[c]);
                float32x4_t lo = vcvtq_f32_u32(vmovl_u16(vget_low_u16(v)));
                float32x4_t hi = vcvtq_f32_u32(vmovl_u16(vget_high_u16(v)));
                vst1_f16(target + c * plane + y * outputWidth + x, vcvt_f16_f32(lo));
                vst1_f16(target + c * plane + y * outputWidth + x + 4, vcvt_f16_f32(hi));
            }
        }
    } else {
        for (int y = 0; y < outputHeight; y++) {
            int sy = yOffset + y * cropHeight / outputHeight;
            for (int x = 0; x < outputWidth; x++) {
                int sx = xOffset + x * cropWidth / outputWidth;
                size_t sourceIndex = static_cast<size_t>(sy) * stride + sx * 3;
                for (int c = 0; c < 3; c++)
                    target[c * plane + y * outputWidth + x] = static_cast<float16_t>(pixels[sourceIndex + c]);
            }
        }
    }
    env->ReleaseByteArrayElements(rgb, source, JNI_ABORT);
}

extern "C" JNIEXPORT void JNICALL Java_com_elabrador_mobilenavigation_NativeSwinlPlan_nativeDecode(
        JNIEnv* env, jclass, jobject maskBytes, jobject confidenceBytes, jintArray mask, jfloatArray confidence) {
    const size_t count = env->GetArrayLength(mask);
    const auto* masks = static_cast<const int32_t*>(env->GetDirectBufferAddress(maskBytes));
    const auto* half = static_cast<const float16_t*>(env->GetDirectBufferAddress(confidenceBytes));
    if (!masks || !half || count == 0 || count > 640 * 480 || env->GetArrayLength(confidence) != count ||
            env->GetDirectBufferCapacity(maskBytes) != count * 4 || env->GetDirectBufferCapacity(confidenceBytes) != count * 2) {
        env->ThrowNew(env->FindClass("java/lang/IllegalArgumentException"), "decode buffer"); return;
    }
    // SetIntArrayRegion avoids pinning/copying an array only to overwrite it.
    env->SetIntArrayRegion(mask, 0, count, masks);
    jfloat* result = env->GetFloatArrayElements(confidence, nullptr);
    if (!result) return;
    size_t i = 0;
    for (; i + 4 <= count; i += 4) vst1q_f32(result + i, vcvt_f32_f16(vld1_f16(half + i)));
    for (; i < count; i++) result[i] = static_cast<float>(half[i]);
    env->ReleaseFloatArrayElements(confidence, result, 0);
}
