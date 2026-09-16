package com.elabrador.mobilenavigation;

import java.util.*;

/** Immutable cloud observation for presentation only; contains no navigation command. */
final class VisionHintSnapshot {
    static final VisionHintSnapshot EMPTY=new VisionHintSnapshot(-1,Collections.emptyMap());
    final long captureMillis;
    final Map<String,Set<String>> objects;

    VisionHintSnapshot(long capture,Map<String,? extends Set<String>> input){
        captureMillis=capture;
        Map<String,Set<String>> copy=new LinkedHashMap<>();
        for(String sector:VisionHintProtocol.SECTORS.keySet()){
            Set<String> types=input.get(sector);
            copy.put(sector,Collections.unmodifiableSet(types==null?new LinkedHashSet<>():new LinkedHashSet<>(types)));
        }
        objects=Collections.unmodifiableMap(copy);
    }
    boolean fresh(long now){
        return captureMillis>=0 && now>=captureMillis && now-captureMillis<VisionHintState.MAX_AGE_MS;
    }
}
