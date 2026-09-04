#include <jni.h>
#include <dlfcn.h>

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <functional>
#include <memory>
#include <sstream>
#include <string>
#include <time.h>
#include <vector>

namespace {

struct ANeuralNetworksTFLite;
struct ANeuralNetworksTFLiteOptions;

using OptionsCreate = int (*)(ANeuralNetworksTFLiteOptions**);
using OptionsFree = int (*)(ANeuralNetworksTFLiteOptions*);
using OptionsSetAccelerationMode = int (*)(ANeuralNetworksTFLiteOptions*, uint32_t);
using OptionsSetPreference = int (*)(ANeuralNetworksTFLiteOptions*, int);
using OptionsSetLowLatency = int (*)(ANeuralNetworksTFLiteOptions*, bool);
using OptionsSetAllowFp16 = int (*)(ANeuralNetworksTFLiteOptions*, bool);
using OptionsSetUseAhwb = int (*)(ANeuralNetworksTFLiteOptions*, bool);
using CreateWithBuffer = int (*)(ANeuralNetworksTFLite**, const char*, size_t,
                                 ANeuralNetworksTFLiteOptions*);
using GetTensorCount = int (*)(ANeuralNetworksTFLite*, uint32_t, int32_t*);
using GetTensorByteSize = int (*)(ANeuralNetworksTFLite*, uint32_t, int, size_t*);
using SetInputTensorData = int (*)(ANeuralNetworksTFLite*, int, const void*, size_t);
using GetOutputTensorData = int (*)(ANeuralNetworksTFLite*, int, void*, size_t);
using Invoke = int (*)(ANeuralNetworksTFLite*);
using IsFullyDelegated = int (*)(ANeuralNetworksTFLite*);
using FreeTflite = int (*)(ANeuralNetworksTFLite*);

template <typename T>
T loadSymbol(void* library, const char* name) {
    dlerror();
    return reinterpret_cast<T>(dlsym(library, name));
}

std::string readModel(const char* path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if (!stream) return {};
    std::streamsize size = stream.tellg();
    if (size <= 0) return {};
    stream.seekg(0, std::ios::beg);
    std::string data(static_cast<size_t>(size), '\0');
    if (!stream.read(&data[0], size)) return {};
    return data;
}

uint64_t hashBytes(const std::vector<uint8_t>& bytes) {
    uint64_t hash = 0xcbf29ce484222325ULL;
    for (uint8_t value : bytes) {
        hash ^= value;
        hash *= 0x100000001b3ULL;
    }
    return hash;
}

void appendLabelComparison(std::ostringstream* report,
                           const std::vector<uint8_t>& output,
                           const std::string& referenceData) {
    if (referenceData.empty()) return;
    if (output.size() % sizeof(float) != 0) {
        *report << "comparison_error=non_float_output\n";
        return;
    }
    const size_t pixelCount = referenceData.size();
    const size_t floatCount = output.size() / sizeof(float);
    if (pixelCount == 0 || floatCount % pixelCount != 0) {
        *report << "comparison_error=reference_shape_mismatch\n";
        return;
    }
    const size_t classCount = floatCount / pixelCount;
    if (classCount == 0 || classCount > 255) {
        *report << "comparison_error=invalid_class_count\n";
        return;
    }

    const auto* logits = reinterpret_cast<const float*>(output.data());
    std::vector<uint8_t> labels(pixelCount, 0);
    std::vector<size_t> predictedCount(classCount, 0);
    std::vector<size_t> referenceCount(classCount, 0);
    std::vector<size_t> intersection(classCount, 0);
    size_t matches = 0;
    for (size_t pixel = 0; pixel < pixelCount; ++pixel) {
        size_t bestClass = 0;
        float bestValue = logits[pixel];
        for (size_t classId = 1; classId < classCount; ++classId) {
            const float candidate = logits[classId * pixelCount + pixel];
            if (candidate > bestValue) {
                bestValue = candidate;
                bestClass = classId;
            }
        }
        labels[pixel] = static_cast<uint8_t>(bestClass);
        ++predictedCount[bestClass];
        const uint8_t reference = static_cast<uint8_t>(referenceData[pixel]);
        if (reference < classCount) {
            ++referenceCount[reference];
            if (bestClass == reference) {
                ++matches;
                ++intersection[reference];
            }
        }
    }

    std::vector<uint8_t> reference(referenceData.begin(), referenceData.end());
    *report << "class_count=" << classCount << '\n';
    *report << "reference_pixels=" << pixelCount << '\n';
    *report << "label_hash=" << std::hex << hashBytes(labels) << std::dec << '\n';
    *report << "reference_label_hash=" << std::hex << hashBytes(reference) << std::dec << '\n';
    *report << "matching_pixels=" << matches << '\n';
    *report << "mismatch_pixels=" << (pixelCount - matches) << '\n';
    *report << "label_agreement=" << (100.0 * matches / pixelCount) << "%\n";
    *report << "center_label=" << static_cast<int>(labels[pixelCount / 2]) << '\n';
    *report << "center_reference="
            << static_cast<int>(static_cast<uint8_t>(referenceData[pixelCount / 2])) << '\n';
    *report << "predicted_counts=";
    for (size_t classId = 0; classId < classCount; ++classId) {
        if (classId != 0) *report << ',';
        *report << classId << ':' << predictedCount[classId];
    }
    *report << '\n';
    *report << "reference_counts=";
    for (size_t classId = 0; classId < classCount; ++classId) {
        if (classId != 0) *report << ',';
        *report << classId << ':' << referenceCount[classId];
    }
    *report << '\n';
    *report << "class_iou=";
    bool first = true;
    for (size_t classId = 0; classId < classCount; ++classId) {
        const size_t unionCount = predictedCount[classId] + referenceCount[classId]
                - intersection[classId];
        if (unionCount == 0) continue;
        if (!first) *report << ',';
        first = false;
        *report << classId << ':' << (100.0 * intersection[classId] / unionCount) << '%';
    }
    *report << '\n';
}

void appendStatus(std::ostringstream* report, const char* name, int status) {
    *report << name << '=' << status << '\n';
}

int64_t monotonicNanos() {
    timespec timestamp{};
    clock_gettime(CLOCK_MONOTONIC, &timestamp);
    return static_cast<int64_t>(timestamp.tv_sec) * 1000000000LL + timestamp.tv_nsec;
}

void throwIllegalState(JNIEnv* env, const std::string& message) {
    jclass exceptionClass = env->FindClass("java/lang/IllegalStateException");
    if (exceptionClass != nullptr) env->ThrowNew(exceptionClass, message.c_str());
}

struct NeuronSession {
    void* library = nullptr;
    std::string model;
    ANeuralNetworksTFLite* tflite = nullptr;
    SetInputTensorData setInputTensorData = nullptr;
    GetOutputTensorData getOutputTensorData = nullptr;
    Invoke invoke = nullptr;
    FreeTflite freeTflite = nullptr;
    size_t inputBytes = 0;
    size_t outputBytes = 0;

    ~NeuronSession() {
        if (tflite != nullptr && freeTflite != nullptr) freeTflite(tflite);
        if (library != nullptr) dlclose(library);
    }
};

}  // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_com_elabrador_mobilenavigation_NativeNeuronShim_nativeCreate(
        JNIEnv* env, jclass, jstring modelPath, jboolean allowFp16) {
    const char* path = env->GetStringUTFChars(modelPath, nullptr);
    std::string modelPathString = path == nullptr ? std::string() : std::string(path);
    if (path != nullptr) env->ReleaseStringUTFChars(modelPath, path);
    if (modelPathString.empty()) {
        throwIllegalState(env, "Neuron model path is empty");
        return 0;
    }

    auto session = std::unique_ptr<NeuronSession>(new NeuronSession());
    session->model = readModel(modelPathString.c_str());
    if (session->model.empty()) {
        throwIllegalState(env, "Neuron model read failed: " + modelPathString);
        return 0;
    }
    session->library = dlopen("libtflite_mtk.mtk.so", RTLD_NOW | RTLD_LOCAL);
    if (session->library == nullptr) {
        session->library = dlopen(
                "/system_ext/lib64/libtflite_mtk.mtk.so", RTLD_NOW | RTLD_LOCAL);
    }
    if (session->library == nullptr) {
        const char* detail = dlerror();
        throwIllegalState(env, std::string("MediaTek TFLite shim unavailable: ")
                + (detail == nullptr ? "unknown" : detail));
        return 0;
    }

    OptionsCreate optionsCreate = loadSymbol<OptionsCreate>(
            session->library, "ANeuroPilotTFLiteOptions_create");
    OptionsFree optionsFree = loadSymbol<OptionsFree>(
            session->library, "ANeuroPilotTFLiteOptions_free");
    OptionsSetAccelerationMode setAccelerationMode = loadSymbol<OptionsSetAccelerationMode>(
            session->library, "ANeuroPilotTFLiteOptions_setAccelerationMode");
    OptionsSetPreference setPreference = loadSymbol<OptionsSetPreference>(
            session->library, "ANeuroPilotTFLiteOptions_setPreference");
    OptionsSetLowLatency setLowLatency = loadSymbol<OptionsSetLowLatency>(
            session->library, "ANeuroPilotTFLiteOptions_setLowLatency");
    OptionsSetAllowFp16 setAllowFp16 = loadSymbol<OptionsSetAllowFp16>(
            session->library, "ANeuroPilotTFLiteOptions_setAllowFp16PrecisionForFp32");
    OptionsSetUseAhwb setUseAhwb = loadSymbol<OptionsSetUseAhwb>(
            session->library, "ANeuroPilotTFLiteOptions_setUseAhwb");
    CreateWithBuffer createWithBuffer = loadSymbol<CreateWithBuffer>(
            session->library, "ANeuroPilotTFLite_createAdvWithBuffer");
    GetTensorByteSize getTensorByteSize = loadSymbol<GetTensorByteSize>(
            session->library, "ANeuroPilotTFLite_getTensorByteSize");
    session->setInputTensorData = loadSymbol<SetInputTensorData>(
            session->library, "ANeuroPilotTFLite_setInputTensorData");
    session->getOutputTensorData = loadSymbol<GetOutputTensorData>(
            session->library, "ANeuroPilotTFLite_getOutputTensorData");
    session->invoke = loadSymbol<Invoke>(session->library, "ANeuroPilotTFLite_invoke");
    session->freeTflite = loadSymbol<FreeTflite>(
            session->library, "ANeuroPilotTFLite_free");
    if (optionsCreate == nullptr || optionsFree == nullptr || setAccelerationMode == nullptr
            || setPreference == nullptr || createWithBuffer == nullptr
            || getTensorByteSize == nullptr || session->setInputTensorData == nullptr
            || session->getOutputTensorData == nullptr || session->invoke == nullptr
            || session->freeTflite == nullptr) {
        throwIllegalState(env, "MediaTek TFLite shim is missing required symbols");
        return 0;
    }

    ANeuralNetworksTFLiteOptions* options = nullptr;
    int status = optionsCreate(&options);
    if (status != 0 || options == nullptr) {
        throwIllegalState(env, "Neuron options creation failed: " + std::to_string(status));
        return 0;
    }
    setAccelerationMode(options, 2);
    setPreference(options, 2);
    if (setLowLatency != nullptr) setLowLatency(options, true);
    if (setAllowFp16 != nullptr) setAllowFp16(options, allowFp16);
    if (setUseAhwb != nullptr) setUseAhwb(options, true);
    status = createWithBuffer(&session->tflite, session->model.data(),
                              session->model.size(), options);
    optionsFree(options);
    if (status != 0 || session->tflite == nullptr) {
        throwIllegalState(env, "Neuron model creation failed: " + std::to_string(status));
        return 0;
    }
    status = getTensorByteSize(session->tflite, 0, 0, &session->inputBytes);
    if (status == 0) {
        status = getTensorByteSize(session->tflite, 1, 0, &session->outputBytes);
    }
    if (status != 0 || session->inputBytes == 0 || session->outputBytes == 0) {
        throwIllegalState(env, "Neuron tensor inspection failed: " + std::to_string(status));
        return 0;
    }
    return reinterpret_cast<jlong>(session.release());
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_elabrador_mobilenavigation_NativeNeuronShim_nativeRun(
        JNIEnv* env, jclass, jlong handle, jobject inputBuffer, jobject outputBuffer) {
    auto* session = reinterpret_cast<NeuronSession*>(handle);
    if (session == nullptr || inputBuffer == nullptr || outputBuffer == nullptr) {
        throwIllegalState(env, "Neuron inference received null state or buffers");
        return -1;
    }
    void* input = env->GetDirectBufferAddress(inputBuffer);
    void* output = env->GetDirectBufferAddress(outputBuffer);
    const jlong inputCapacity = env->GetDirectBufferCapacity(inputBuffer);
    const jlong outputCapacity = env->GetDirectBufferCapacity(outputBuffer);
    if (input == nullptr || output == nullptr
            || inputCapacity < static_cast<jlong>(session->inputBytes)
            || outputCapacity < static_cast<jlong>(session->outputBytes)) {
        throwIllegalState(env, "Neuron direct buffers have invalid capacity");
        return -1;
    }
    int status = session->setInputTensorData(
            session->tflite, 0, input, session->inputBytes);
    if (status != 0) {
        throwIllegalState(env, "Neuron input upload failed: " + std::to_string(status));
        return -1;
    }
    const int64_t started = monotonicNanos();
    status = session->invoke(session->tflite);
    const int64_t elapsed = monotonicNanos() - started;
    if (status != 0) {
        throwIllegalState(env, "Neuron invoke failed: " + std::to_string(status));
        return -1;
    }
    status = session->getOutputTensorData(
            session->tflite, 0, output, session->outputBytes);
    if (status != 0) {
        throwIllegalState(env, "Neuron output download failed: " + std::to_string(status));
        return -1;
    }
    return static_cast<jlong>(elapsed);
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_NativeNeuronShim_nativeDestroy(
        JNIEnv*, jclass, jlong handle) {
    delete reinterpret_cast<NeuronSession*>(handle);
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_elabrador_mobilenavigation_NativeNeuronShim_nativeBenchmark(
        JNIEnv* env, jclass, jstring modelPath, jint warmupRuns, jint runs, jboolean allowFp16) {
    const char* path = env->GetStringUTFChars(modelPath, nullptr);
    const std::string modelPathString = path == nullptr ? std::string() : std::string(path);
    std::string model = modelPathString.empty() ? std::string() : readModel(modelPathString.c_str());
    if (path != nullptr) env->ReleaseStringUTFChars(modelPath, path);

    std::ostringstream report;
    report << "backend=MEDIATEK_TFLITE_SHIM_NEURON\n";
    report << "model_bytes=" << model.size() << '\n';
    if (model.empty()) {
        report << "error=model_read_failed\n";
        return env->NewStringUTF(report.str().c_str());
    }

    void* library = dlopen("libtflite_mtk.mtk.so", RTLD_NOW | RTLD_LOCAL);
    if (library == nullptr) {
        library = dlopen("/system_ext/lib64/libtflite_mtk.mtk.so", RTLD_NOW | RTLD_LOCAL);
    }
    if (library == nullptr) {
        const char* error = dlerror();
        report << "error=shim_dlopen_failed";
        if (error != nullptr) report << " detail=" << error;
        report << '\n';
        return env->NewStringUTF(report.str().c_str());
    }

    OptionsCreate optionsCreate = loadSymbol<OptionsCreate>(
            library, "ANeuroPilotTFLiteOptions_create");
    OptionsFree optionsFree = loadSymbol<OptionsFree>(
            library, "ANeuroPilotTFLiteOptions_free");
    OptionsSetAccelerationMode setAccelerationMode = loadSymbol<OptionsSetAccelerationMode>(
            library, "ANeuroPilotTFLiteOptions_setAccelerationMode");
    OptionsSetPreference setPreference = loadSymbol<OptionsSetPreference>(
            library, "ANeuroPilotTFLiteOptions_setPreference");
    OptionsSetLowLatency setLowLatency = loadSymbol<OptionsSetLowLatency>(
            library, "ANeuroPilotTFLiteOptions_setLowLatency");
    OptionsSetAllowFp16 setAllowFp16 = loadSymbol<OptionsSetAllowFp16>(
            library, "ANeuroPilotTFLiteOptions_setAllowFp16PrecisionForFp32");
    OptionsSetUseAhwb setUseAhwb = loadSymbol<OptionsSetUseAhwb>(
            library, "ANeuroPilotTFLiteOptions_setUseAhwb");
    CreateWithBuffer createWithBuffer = loadSymbol<CreateWithBuffer>(
            library, "ANeuroPilotTFLite_createAdvWithBuffer");
    GetTensorCount getTensorCount = loadSymbol<GetTensorCount>(
            library, "ANeuroPilotTFLite_getTensorCount");
    GetTensorByteSize getTensorByteSize = loadSymbol<GetTensorByteSize>(
            library, "ANeuroPilotTFLite_getTensorByteSize");
    SetInputTensorData setInputTensorData = loadSymbol<SetInputTensorData>(
            library, "ANeuroPilotTFLite_setInputTensorData");
    GetOutputTensorData getOutputTensorData = loadSymbol<GetOutputTensorData>(
            library, "ANeuroPilotTFLite_getOutputTensorData");
    Invoke invoke = loadSymbol<Invoke>(library, "ANeuroPilotTFLite_invoke");
    FreeTflite freeTflite = loadSymbol<FreeTflite>(library, "ANeuroPilotTFLite_free");
    if (optionsCreate == nullptr || optionsFree == nullptr || setAccelerationMode == nullptr
            || setPreference == nullptr || createWithBuffer == nullptr || getTensorByteSize == nullptr
            || setInputTensorData == nullptr || getOutputTensorData == nullptr
            || invoke == nullptr || freeTflite == nullptr) {
        report << "error=shim_required_symbol_missing\n";
        dlclose(library);
        return env->NewStringUTF(report.str().c_str());
    }

    ANeuralNetworksTFLiteOptions* options = nullptr;
    int status = optionsCreate(&options);
    appendStatus(&report, "options_create", status);
    if (status != 0 || options == nullptr) {
        report << "error=options_create_failed\n";
        dlclose(library);
        return env->NewStringUTF(report.str().c_str());
    }
    appendStatus(&report, "set_acceleration_neuron", setAccelerationMode(options, 2));
    appendStatus(&report, "set_preference_sustained", setPreference(options, 2));
    if (setLowLatency != nullptr) appendStatus(&report, "set_low_latency", setLowLatency(options, true));
    if (setAllowFp16 != nullptr) appendStatus(
            &report, "set_allow_fp16", setAllowFp16(options, allowFp16));
    if (setUseAhwb != nullptr) appendStatus(&report, "set_use_ahwb", setUseAhwb(options, true));

    ANeuralNetworksTFLite* tflite = nullptr;
    status = createWithBuffer(&tflite, model.data(), model.size(), options);
    appendStatus(&report, "create_neuron_model", status);
    optionsFree(options);
    if (status != 0 || tflite == nullptr) {
        report << "error=create_model_failed\n";
        dlclose(library);
        return env->NewStringUTF(report.str().c_str());
    }

    int32_t inputCount = 0;
    int32_t outputCount = 0;
    if (getTensorCount != nullptr) {
        appendStatus(&report, "input_count", getTensorCount(tflite, 0, &inputCount));
        appendStatus(&report, "output_count", getTensorCount(tflite, 1, &outputCount));
    }
    size_t inputBytes = 0;
    size_t outputBytes = 0;
    appendStatus(&report, "input_bytes", getTensorByteSize(tflite, 0, 0, &inputBytes));
    appendStatus(&report, "output_bytes", getTensorByteSize(tflite, 1, 0, &outputBytes));
    report << "input_bytes_value=" << inputBytes << '\n';
    report << "output_bytes_value=" << outputBytes << '\n';
    std::vector<uint8_t> input(inputBytes, 0);
    const std::string inputData = readModel((modelPathString + ".input").c_str());
    if (inputData.size() == input.size()) {
        std::copy(inputData.begin(), inputData.end(), input.begin());
        report << "input_source=companion_file\n";
    } else {
        for (size_t i = 0; i < input.size(); ++i) {
            input[i] = static_cast<uint8_t>((i * 37 + 11) & 0xff);
        }
        report << "input_source=deterministic_bytes\n";
        if (!inputData.empty()) report << "input_file_size_mismatch=" << inputData.size() << '\n';
    }
    std::vector<uint8_t> output(outputBytes, 0);
    appendStatus(&report, "set_input", setInputTensorData(tflite, 0, input.data(), input.size()));

    int warmup = std::max(0, static_cast<int>(warmupRuns));
    int iterations = std::max(1, static_cast<int>(runs));
    for (int i = 0; i < warmup; ++i) {
        int warmupStatus = invoke(tflite);
        if (warmupStatus != 0) appendStatus(&report, "warmup_invoke", warmupStatus);
    }
    std::vector<long long> durations;
    std::vector<uint64_t> outputHashes;
    durations.reserve(iterations);
    outputHashes.reserve(iterations);
    int outputStatus = 0;
    for (int i = 0; i < iterations; ++i) {
        int64_t start = monotonicNanos();
        int invokeStatus = invoke(tflite);
        int64_t elapsed = monotonicNanos() - start;
        durations.push_back(static_cast<long long>(elapsed));
        if (invokeStatus != 0) appendStatus(&report, "invoke", invokeStatus);
        outputStatus = getOutputTensorData(tflite, 0, output.data(), output.size());
        if (outputStatus != 0) appendStatus(&report, "get_output", outputStatus);
        outputHashes.push_back(hashBytes(output));
    }
    appendStatus(&report, "get_output", outputStatus);
    // The Android 16 MT6989 vendor shim crashes inside isFullyDelegated() for
    // valid partially delegated graphs. Delegation coverage is read from the
    // TFLite log instead; the query is not required for inference.
    std::sort(durations.begin(), durations.end());
    report << "runs=" << iterations << '\n';
    report << "median_ms=" << durations[durations.size() / 2] / 1000000.0 << '\n';
    report << "best_ms=" << durations.front() / 1000000.0 << '\n';
    report << "output_hash=" << std::hex << hashBytes(output) << std::dec << '\n';
    report << "output_hashes=";
    for (size_t index = 0; index < outputHashes.size(); ++index) {
        if (index != 0) report << ',';
        report << std::hex << outputHashes[index] << std::dec;
    }
    report << '\n';
    report << "output_stable="
            << (std::adjacent_find(outputHashes.begin(), outputHashes.end(),
                                   std::not_equal_to<uint64_t>()) == outputHashes.end())
            << '\n';
    appendLabelComparison(&report, output, readModel((modelPathString + ".labels").c_str()));
    freeTflite(tflite);
    dlclose(library);
    return env->NewStringUTF(report.str().c_str());
}
