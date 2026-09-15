package com.elabrador.mobilenavigation;

/** 30Hz streams: refuse a depth frame from a different clock or adjacent period. */
final class FramePairTiming {
    static boolean valid(boolean sameClock,double skewMillis){
        return sameClock&&Double.isFinite(skewMillis)&&skewMillis>=0&&skewMillis<=20;
    }
}
