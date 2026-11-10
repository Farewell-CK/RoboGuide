// Executes a pre-lowered multi-subgraph plan exclusively on mtk-mdla_shim.
// The host side only allocates and copies buffers; every arithmetic op is
// submitted to the NPU driver through ANeuralNetworksCompilation_createForDevices.
#include <jni.h>
#include <android/NeuralNetworks.h>
#include <android/log.h>
#include <android/sharedmem.h>
#include <sys/mman.h>
#include <unistd.h>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <algorithm>
#include <unordered_map>
#include <future>
#include <functional>
#include <dlfcn.h>

namespace {

void ok(int status, const std::string& stage) {
    if (status) throw std::runtime_error(stage + " status=" + std::to_string(status));
}

struct Reader {
    std::ifstream file;
    explicit Reader(const std::string& path) : file(path, std::ios::binary) {
        if (!file) throw std::runtime_error("plan file missing: " + path);
    }
    template <class T> T get() {
        T value{};
        if (!file.read(reinterpret_cast<char*>(&value), sizeof(T))) throw std::runtime_error("plan truncated");
        return value;
    }
    void raw(void* out, size_t size) {
        if (size && !file.read(reinterpret_cast<char*>(out), size)) throw std::runtime_error("plan truncated");
    }
    std::string text() {
        uint32_t length = get<uint32_t>();
        if (length > 4096) throw std::runtime_error("plan name too long");
        std::string out(length, '\0');
        raw(out.data(), length);
        return out;
    }
};

size_t element_width(int32_t type) {
    switch (type) {
        case ANEURALNETWORKS_TENSOR_FLOAT16: return 2;
        case ANEURALNETWORKS_TENSOR_FLOAT32:
        case ANEURALNETWORKS_TENSOR_INT32: return 4;
        default: return 1;
    }
}

size_t byte_size(int32_t type, const std::vector<uint32_t>& dims) {
    size_t count = 1;
    for (uint32_t d : dims) count *= d;
    return count * element_width(type);
}

// Bind the same shared allocation at producer and consumer boundaries. Pointer
// bindings force NNAPI to copy each tensor into temporary IPC memory per call.
struct SharedBuffer {
    int fd = -1;
    void* address = nullptr;
    size_t length = 0;
    ANeuralNetworksMemory* memory = nullptr;
    SharedBuffer() = default;
    SharedBuffer(const SharedBuffer&) = delete;
    SharedBuffer& operator=(const SharedBuffer&) = delete;
    SharedBuffer(SharedBuffer&& other) noexcept
        : fd(other.fd), address(other.address), length(other.length), memory(other.memory) {
        other.fd = -1; other.address = nullptr; other.length = 0; other.memory = nullptr;
    }
    SharedBuffer& operator=(SharedBuffer&& other) noexcept {
        if (this != &other) {
            reset();
            fd = other.fd; address = other.address; length = other.length; memory = other.memory;
            other.fd = -1; other.address = nullptr; other.length = 0; other.memory = nullptr;
        }
        return *this;
    }
    ~SharedBuffer() { reset(); }
    bool empty() const { return length == 0; }
    size_t size() const { return length; }
    uint8_t* data() { return static_cast<uint8_t*>(address); }
    const uint8_t* data() const { return static_cast<const uint8_t*>(address); }
    void reset() {
        if (memory) ANeuralNetworksMemory_free(memory);
        if (address) munmap(address, length);
        if (fd >= 0) close(fd);
        fd = -1; address = nullptr; length = 0; memory = nullptr;
    }
    void resize(size_t bytes) {
        if (length == bytes) return;
        reset();
        fd = ASharedMemory_create("swin-plan", bytes);
        if (fd < 0) throw std::runtime_error("shared allocation failed");
        address = mmap(nullptr, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (address == MAP_FAILED) { address = nullptr; reset(); throw std::runtime_error("shared mapping failed"); }
        length = bytes;
        ok(ANeuralNetworksMemory_createFromFd(bytes, PROT_READ | PROT_WRITE, fd, 0, &memory), "shared NNAPI memory");
    }
};


// Finite, deterministic filler so capability probes never trip on NaN/Inf.
void fill_pattern(SharedBuffer& data, int32_t type) {
    if (type == ANEURALNETWORKS_TENSOR_FLOAT16) {
        auto* values = reinterpret_cast<_Float16*>(data.data());
        size_t count = data.size() / 2;
        for (size_t i = 0; i < count; i++) values[i] = static_cast<_Float16>(0.05f + 0.3f * std::sin((double)(i % 97) * 0.07));
    } else if (type == ANEURALNETWORKS_TENSOR_FLOAT32) {
        auto* values = reinterpret_cast<float*>(data.data());
        size_t count = data.size() / 4;
        for (size_t i = 0; i < count; i++) values[i] = 0.05f + 0.3f * std::sin((double)(i % 97) * 0.07);
    } else if (type == ANEURALNETWORKS_TENSOR_INT32) {
        auto* values = reinterpret_cast<int32_t*>(data.data());
        size_t count = data.size() / 4;
        for (size_t i = 0; i < count; i++) values[i] = static_cast<int32_t>(i % 5);
    }
}

struct BufferDesc { std::string name; int32_t type; std::vector<uint32_t> dims; };
struct ValueDesc { int32_t type; std::vector<uint32_t> dims; std::vector<uint8_t> data; uint32_t binding; };
struct OpDesc { int32_t code; std::vector<uint32_t> in, out; };
struct Subgraph { std::vector<ValueDesc> values; std::vector<OpDesc> ops; std::vector<uint32_t> inputs, outputs; };

struct RuntimeOptions {
    int preference;
    bool burst;
    bool prefetch;
    bool costCache;
    std::string target;
    bool highPriority;
    bool measureTiming;
};

struct ModelHolder {
    bool ready = false;
    ANeuralNetworksModel* model = nullptr;
    ANeuralNetworksCompilation* compilation = nullptr;
    ANeuralNetworksExecution* execution = nullptr;
    ANeuralNetworksBurst* burst = nullptr;
    ~ModelHolder() {
        if (execution) ANeuralNetworksExecution_free(execution);
        if (burst) ANeuralNetworksBurst_free(burst);
        if (compilation) ANeuralNetworksCompilation_free(compilation);
        if (model) ANeuralNetworksModel_free(model);
    }
};

const ANeuralNetworksDevice* accelerator_device(const std::string& target) {
    if (target != "mtk-mdla_shim" && target != "mtk-neuron_shim" && target != "mtk-dsp_shim")
        throw std::runtime_error("unapproved accelerator target; CPU fallback forbidden");
    uint32_t count = 0;
    ok(ANeuralNetworks_getDeviceCount(&count), "getDeviceCount");
    for (uint32_t i = 0; i < count; i++) {
        ANeuralNetworksDevice* device = nullptr;
        const char* name = nullptr;
        ok(ANeuralNetworks_getDevice(i, &device), "getDevice");
        ok(ANeuralNetworksDevice_getName(device, &name), "getDeviceName");
        if (std::string(name) == target) {
            int32_t type = 0; int64_t level = 0; const char* version = "unknown";
            ANeuralNetworksDevice_getType(device, &type);
            if (type != ANEURALNETWORKS_DEVICE_ACCELERATOR) throw std::runtime_error("target is not a dedicated accelerator");
            ANeuralNetworksDevice_getFeatureLevel(device, &level);
            ANeuralNetworksDevice_getVersion(device, &version);
            __android_log_print(ANDROID_LOG_INFO, "SwinAudit", "PLAN_DEVICE name=%s type=%d feature=%lld version=%s", name, type, static_cast<long long>(level), version);
            return device;
        }
    }
    throw std::runtime_error("accelerator absent; CPU fallback forbidden");
}

struct SubgraphResult {
    bool reused = false;
    bool ok = false;
    uint32_t accepted = 0;
    double compileMs = 0;
    double computeMs = 0;
    double modelMs = 0;
    double queryMs = 0;
    double ioMs = 0;
    double hardwareMs = 0;
    double driverMs = 0;
    std::string error;
    std::vector<uint32_t> rejectedOps;
};

// Exact structural keys omit external buffer IDs, allowing the same compiled
// arithmetic template to process different values. Learned-weight models are
// excluded: retaining all of Swin exhausts the driver's DMA allocation budget.
std::string template_key(const Subgraph& sub) {
    size_t constants = 0;
    for (const auto& v : sub.values) constants += v.data.size();
    if (constants > 32768) return {};
    std::string key;
    auto put = [&](uint32_t v) { key.append(reinterpret_cast<const char*>(&v), sizeof(v)); };
    auto indices = [&](const std::vector<uint32_t>& values) { put(values.size()); for (auto v : values) put(v); };
    put(sub.values.size());
    for (const auto& v : sub.values) {
        put(v.type); indices(v.dims); put(v.data.size());
        if (!v.data.empty()) key.append(reinterpret_cast<const char*>(v.data.data()), v.data.size());
    }
    put(sub.ops.size());
    for (const auto& op : sub.ops) { put(op.code); indices(op.in); indices(op.out); }
    indices(sub.inputs); indices(sub.outputs);
    return key;
}

struct TemplateCache {
    struct Entry { std::string key; std::unique_ptr<ModelHolder> model; uint64_t used = 0; };
    size_t capacity;
    uint64_t clock = 0;
    std::vector<Entry> entries;
    explicit TemplateCache(size_t slots) : capacity(slots) { entries.reserve(slots); }
    void clear() { entries.clear(); }
    bool ready(const std::string& key) const {
        if (key.empty()) return false;
        for (const auto& entry : entries) if (entry.key == key && entry.model && entry.model->ready) return true;
        return false;
    }
    std::unique_ptr<ModelHolder>* slot(const std::string& key) {
        if (key.empty() || capacity == 0) return nullptr;
        for (auto& entry : entries) if (entry.key == key) { entry.used = ++clock; return &entry.model; }
        Entry* chosen;
        if (entries.size() < capacity) { entries.emplace_back(); chosen = &entries.back(); }
        else chosen = &*std::min_element(entries.begin(), entries.end(), [](const Entry& a, const Entry& b) { return a.used < b.used; });
        chosen->model.reset(); chosen->key = key; chosen->used = ++clock;
        return &chosen->model;
    }
};


// NNAPI compilation cache: the driver stores the compiled artifact keyed by a
// 32-byte token, so repeat runs skip the (very slow) MDLA compiler.
void cache_token(const Subgraph& sub, uint8_t token[32], const RuntimeOptions& options) {
    uint64_t hash = 1469598103934665603ULL;
    auto mix = [&hash](const void* data, size_t size) {
        auto* bytes = static_cast<const uint8_t*>(data);
        for (size_t i = 0; i < size; i++) { hash ^= bytes[i]; hash *= 1099511628211ULL; }
    };
    auto indices = [&mix](const std::vector<uint32_t>& values) {
        uint32_t count = static_cast<uint32_t>(values.size());
        mix(&count, sizeof(count));
        if (!values.empty()) mix(values.data(), values.size() * sizeof(uint32_t));
    };
    uint32_t revision = 2;
    mix(&revision, sizeof(revision));
    // Keep legacy FAST_SINGLE_ANSWER tokens valid; other policies get distinct
    // compiled artifacts because the vendor may bake its policy into the cache.
    if (options.preference != ANEURALNETWORKS_PREFER_FAST_SINGLE_ANSWER)
        mix(&options.preference, sizeof(options.preference));
    if (options.target != "mtk-mdla_shim") mix(options.target.data(), options.target.size());
    if (options.highPriority) {
        int32_t priority = ANEURALNETWORKS_PRIORITY_HIGH;
        mix(&priority, sizeof(priority));
    }
    uint32_t valueCount = static_cast<uint32_t>(sub.values.size());
    mix(&valueCount, sizeof(valueCount));
    for (const auto& value : sub.values) {
        mix(&value.type, sizeof(value.type));
        indices(value.dims);
        uint64_t bytes = value.data.size();
        mix(&bytes, sizeof(bytes));
        if (!value.data.empty()) mix(value.data.data(), value.data.size());
    }
    uint32_t opCount = static_cast<uint32_t>(sub.ops.size());
    mix(&opCount, sizeof(opCount));
    for (const auto& op : sub.ops) {
        mix(&op.code, sizeof(op.code));
        indices(op.in); indices(op.out);
    }
    indices(sub.inputs); indices(sub.outputs);
    for (int i = 0; i < 4; i++) {
        std::memcpy(token + i * 8, &hash, 8);
        hash = hash * 6364136223846793005ULL + 1442695040888963407ULL;
    }
}

SubgraphResult execute_subgraph(const Subgraph& sub, const ANeuralNetworksDevice* device,
                                std::vector<SharedBuffer>& buffers, uint32_t index, bool queryOnly, bool compileOnly,
                                const std::string& cacheDir, bool auditSupport, const RuntimeOptions& options,
                                std::unique_ptr<ModelHolder>* resident = nullptr,
                                const std::function<void()>& beforeCompute = {}, bool internalPreparation = false) {
    SubgraphResult result;
    try {
        ModelHolder temporary;
        if (resident && !*resident) resident->reset(new ModelHolder());
        ModelHolder& holder = resident ? **resident : temporary;
        if (!holder.ready) {
        auto modelStart = std::chrono::steady_clock::now();
        ok(ANeuralNetworksModel_create(&holder.model), "model create");
        for (uint32_t v = 0; v < sub.values.size(); v++) {
            const ValueDesc& value = sub.values[v];
            static uint32_t emptyDims[6] = {0, 0, 0, 0, 0, 0};
            ANeuralNetworksOperandType type{value.type, static_cast<uint32_t>(value.dims.size()),
                                             value.dims.empty() ? nullptr : value.dims.data(),
                                             0.f, 0};
            ok(ANeuralNetworksModel_addOperand(holder.model, &type),
               "add operand index=" + std::to_string(v) + " type=" + std::to_string(value.type) +
                   " rank=" + std::to_string(value.dims.size()));
            if (!value.data.empty())
                ok(ANeuralNetworksModel_setOperandValue(holder.model, v, value.data.data(), value.data.size()),
                   "constant value");
        }
        for (uint32_t o = 0; o < sub.ops.size(); o++) {
            const OpDesc& op = sub.ops[o];
            ok(ANeuralNetworksModel_addOperation(holder.model, op.code, op.in.size(),
                                                 const_cast<uint32_t*>(op.in.data()), op.out.size(),
                                                 const_cast<uint32_t*>(op.out.data())),
               "add operation index=" + std::to_string(o) + " opcode=" + std::to_string(op.code));
        }
        ok(ANeuralNetworksModel_identifyInputsAndOutputs(holder.model, sub.inputs.size(),
                                                        const_cast<uint32_t*>(sub.inputs.data()),
                                                        sub.outputs.size(),
                                                        const_cast<uint32_t*>(sub.outputs.data())),
           "identify IO");
        ok(ANeuralNetworksModel_finish(holder.model), "finish");
        result.modelMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - modelStart).count();

        if (auditSupport || queryOnly || (compileOnly && !internalPreparation)) {
        auto queryStart = std::chrono::steady_clock::now();
        std::unique_ptr<bool[]> supported(new bool[sub.ops.size()]());
        ok(ANeuralNetworksModel_getSupportedOperationsForDevices(holder.model, &device, 1, supported.get()),
           "support query");
        for (size_t o = 0; o < sub.ops.size(); o++) {
            if (supported[o]) result.accepted++;
            else result.rejectedOps.push_back(static_cast<uint32_t>(o));
        }
        result.queryMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - queryStart).count();
        }
        result.ok = true;
        if (queryOnly || ((auditSupport || (compileOnly && !internalPreparation)) && result.accepted != sub.ops.size())) return result;

        auto compileStart = std::chrono::steady_clock::now();
        ok(ANeuralNetworksCompilation_createForDevices(holder.model, &device, 1, &holder.compilation),
           "compile create");
        ok(ANeuralNetworksCompilation_setPreference(holder.compilation, options.preference), "compile preference");
        if (options.highPriority) {
            using SetPriority = int (*)(ANeuralNetworksCompilation*, int);
            auto setPriority = reinterpret_cast<SetPriority>(dlsym(RTLD_DEFAULT, "ANeuralNetworksCompilation_setPriority"));
            if (!setPriority) throw std::runtime_error("NNAPI priority API unavailable");
            ok(setPriority(holder.compilation, ANEURALNETWORKS_PRIORITY_HIGH), "compile priority");
        }
        {
            uint8_t token[32];
            cache_token(sub, token, options);
            std::string cache_dir = cacheDir;
            int cache_status = ANeuralNetworksCompilation_setCaching(holder.compilation, cache_dir.c_str(), token);
            if (cache_status != ANEURALNETWORKS_NO_ERROR)
                __android_log_print(ANDROID_LOG_WARN, "SwinAudit", "PLAN_CACHE_UNAVAILABLE status=%d", cache_status);
        }
        ok(ANeuralNetworksCompilation_finish(holder.compilation), "compile finish");
        result.compileMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - compileStart).count();
        result.accepted = static_cast<uint32_t>(sub.ops.size());
        holder.ready = true;
        } else {
            result.reused = true;
            result.accepted = static_cast<uint32_t>(sub.ops.size());
            result.ok = true;
        }

        if (compileOnly) return result;
        if (beforeCompute) beforeCompute();
        auto ioStart = std::chrono::steady_clock::now();
        ok(ANeuralNetworksExecution_create(holder.compilation, &holder.execution), "execution create");
        // Hardware duration queries are useful for offline audits but add driver
        // bookkeeping to every one of the 79 executions. Production inference
        // keeps wall-clock timing around the compute call and disables this.
        int timingStatus = options.measureTiming
                ? ANeuralNetworksExecution_setMeasureTiming(holder.execution, true)
                : ANEURALNETWORKS_BAD_STATE;
        for (uint32_t i = 0; i < sub.inputs.size(); i++) {
            const ValueDesc& value = sub.values[sub.inputs[i]];
            if (value.binding == 0xFFFFFFFFu) throw std::runtime_error("unbound subgraph input");
            SharedBuffer& buffer = buffers[value.binding];
            if (buffer.empty()) {
                throw std::runtime_error("missing production input buffer");
            }
            if (buffer.size() != byte_size(value.type, value.dims))
                throw std::runtime_error("input buffer size mismatch");
            ok(ANeuralNetworksExecution_setInputFromMemory(holder.execution, i, nullptr, buffer.memory, 0, buffer.size()),
               "set input");
        }
        for (uint32_t i = 0; i < sub.outputs.size(); i++) {
            const ValueDesc& value = sub.values[sub.outputs[i]];
            if (value.binding == 0xFFFFFFFFu) throw std::runtime_error("unbound subgraph output");
            SharedBuffer& buffer = buffers[value.binding];
            if (buffer.empty()) buffer.resize(byte_size(value.type, value.dims));
            if (buffer.size() != byte_size(value.type, value.dims))
                throw std::runtime_error("output buffer size mismatch");
            ok(ANeuralNetworksExecution_setOutputFromMemory(holder.execution, i, nullptr, buffer.memory, 0, buffer.size()),
               "set output");
        }
        auto computeStart = std::chrono::steady_clock::now();
        result.ioMs = std::chrono::duration<double, std::milli>(computeStart - ioStart).count();
        if (options.burst) {
            if (!holder.burst) ok(ANeuralNetworksBurst_create(holder.compilation, &holder.burst), "burst create");
            ok(ANeuralNetworksExecution_burstCompute(holder.execution, holder.burst), "burst compute");
        } else {
            ok(ANeuralNetworksExecution_compute(holder.execution), "compute");
        }
        result.computeMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - computeStart).count();
        if (timingStatus == ANEURALNETWORKS_NO_ERROR) {
            uint64_t duration = UINT64_MAX;
            if (ANeuralNetworksExecution_getDuration(holder.execution, ANEURALNETWORKS_DURATION_ON_HARDWARE, &duration) == 0 && duration != UINT64_MAX)
                result.hardwareMs = duration / 1000000.0;
            duration = UINT64_MAX;
            if (ANeuralNetworksExecution_getDuration(holder.execution, ANEURALNETWORKS_DURATION_IN_DRIVER, &duration) == 0 && duration != UINT64_MAX)
                result.driverMs = duration / 1000000.0;
        }
        ANeuralNetworksExecution_free(holder.execution);
        holder.execution = nullptr;
        return result;
    } catch (const std::exception& error) {
        result.ok = false;
        result.error = error.what();
        __android_log_print(ANDROID_LOG_ERROR, "SwinAudit", "PLAN_SUBGRAPH_FAILED sub=%u error=%s", index, error.what());
        return result;
    }
}


} // namespace
