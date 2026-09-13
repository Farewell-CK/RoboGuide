package com.elabrador.mobilenavigation;

/** Read-only probe for MediaTek's system Neuron/TFLite Shim libraries. */
public final class NativeNeuronProbe {
    static {
        System.loadLibrary("elabrador_native");
    }

    private NativeNeuronProbe() {}

    /**
     * Loads no model and invokes no vendor entry point. The returned report only contains
     * library load and symbol-presence information.
     */
    public static native String nativeProbe();
}
