package com.elabrador.mobilenavigation;

/** Measured wall-clock stages for one real camera frame; never derived from UI redraws. */
final class InferenceTiming {
    final FrameEvidence evidence;
    final long prepNanos,modelNanos,decodeNanos,mapNanos;
    InferenceTiming(FrameEvidence evidence,long prep,long model,long decode,long map) {
        this.evidence=evidence;prepNanos=prep;modelNanos=model;decodeNanos=decode;mapNanos=map;
    }
    double modelMillis(){return modelNanos/1e6;}
    double pipelineMillis(){return (prepNanos+modelNanos+decodeNanos+mapNanos)/1e6;}
}
