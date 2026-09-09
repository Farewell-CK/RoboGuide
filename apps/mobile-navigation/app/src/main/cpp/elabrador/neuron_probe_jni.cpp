#include <jni.h>
#include <dlfcn.h>
#include <sstream>
#include <string>

namespace {

struct LibraryProbe {
    const char* name;
    void* handle = nullptr;
    std::string error;
};

void probeLibrary(LibraryProbe* library) {
    dlerror();
    library->handle = dlopen(library->name, RTLD_NOW | RTLD_LOCAL);
    if (library->handle == nullptr) {
        const char* error = dlerror();
        library->error = error == nullptr ? "unknown dlopen error" : error;
        std::string absolute = "/system_ext/lib64/";
        absolute += library->name;
        dlerror();
        library->handle = dlopen(absolute.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (library->handle != nullptr) {
            library->error = "loaded_by_absolute_path";
        } else {
            const char* absoluteError = dlerror();
            if (absoluteError != nullptr) {
                library->error += "; absolute=";
                library->error += absoluteError;
            }
        }
    }
}

bool hasSymbol(void* handle, const char* symbol) {
    if (handle == nullptr) return false;
    dlerror();
    void* address = dlsym(handle, symbol);
    return address != nullptr && dlerror() == nullptr;
}

void closeLibrary(LibraryProbe* library) {
    if (library->handle != nullptr) {
        dlclose(library->handle);
        library->handle = nullptr;
    }
}

void appendLibrary(std::ostringstream* report, const LibraryProbe& library) {
    *report << "library=" << library.name << " loaded="
            << (library.handle != nullptr ? "true" : "false");
    if (!library.error.empty()) *report << " error=" << library.error;
    *report << '\n';
}

void appendSymbol(std::ostringstream* report, void* handle, const char* symbol) {
    *report << "symbol=" << symbol << " present="
            << (hasSymbol(handle, symbol) ? "true" : "false") << '\n';
}

}  // namespace

extern "C" JNIEXPORT jstring JNICALL
Java_com_elabrador_mobilenavigation_NativeNeuronProbe_nativeProbe(JNIEnv* env, jclass) {
    LibraryProbe shim{"libtflite_mtk.mtk.so"};
    LibraryProbe graph{"libneuron_graph_delegate.mtk.so"};
    LibraryProbe adapter{"libneuronusdk_adapter.mtk.so"};
    probeLibrary(&shim);
    probeLibrary(&graph);
    probeLibrary(&adapter);

    std::ostringstream report;
    report << "probe_version=1\n";
    appendLibrary(&report, shim);
    appendLibrary(&report, graph);
    appendLibrary(&report, adapter);

    // The public docs use ANeuralNetworksTFLite* typedefs, while the MT6989
    // system image exports the same ABI with the ANeuroPilotTFLite* prefix.
    // Probe the exported names so the result reflects the device, not the
    // documentation spelling.
    const char* shimSymbols[] = {
        "ANeuroPilotTFLiteOptions_create",
        "ANeuroPilotTFLiteOptions_setAccelerationMode",
        "ANeuroPilotTFLiteOptions_setLowLatency",
        "ANeuroPilotTFLiteOptions_setPreference",
        "ANeuroPilotTFLiteOptions_setAllowFp16PrecisionForFp32",
        "ANeuroPilotTFLiteOptions_setUseAhwb",
        "ANeuroPilotTFLiteWrapper_makeAdvTFLiteWithBuffer",
        "ANeuroPilotTFLiteWrapper_invoke",
        "ANeuroPilotTFLiteWrapper_free",
        "ANeuroPilotTFLite_createAdvWithBuffer",
        "ANeuroPilotTFLite_invoke",
        "ANeuroPilotTFLite_isFullyDelegated",
        "ANeuroPilotTFLite_setInputTensorData",
        "ANeuroPilotTFLite_getOutputTensorData",
    };
    for (const char* symbol : shimSymbols) appendSymbol(&report, shim.handle, symbol);

    // Keep the spelling mismatch visible in the report for future debugging.
    appendSymbol(&report, shim.handle, "ANeuralNetworksTFLiteOptions_create");
    report << "docs_typedef_prefix=ANeuralNetworksTFLite\n";
    report << "device_export_prefix=ANeuroPilotTFLite\n";

    appendSymbol(&report, graph.handle, "MtkGraph_freeNeuronGraph");
    report << "recommendation="
           << (shim.handle != nullptr && hasSymbol(
                   shim.handle, "ANeuroPilotTFLite_createAdvWithBuffer")
                   ? "direct_tflite_shim_candidate" : "keep_nnapi_gpu_path") << '\n';

    closeLibrary(&adapter);
    closeLibrary(&graph);
    closeLibrary(&shim);
    return env->NewStringUTF(report.str().c_str());
}
