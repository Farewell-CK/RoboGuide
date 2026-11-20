package com.elabrador.mobilenavigation;

import com.intel.realsense.librealsense.Intrinsic;
import org.json.JSONObject;

/** Owned acquisition pair; retained by the single pending inference and never re-sampled later. */
final class InferenceCapture {
    final DepthImage raw;
    final JSONObject metadata;
    InferenceCapture(DepthImage raw,JSONObject metadata){this.raw=raw;this.metadata=metadata;}
    static JSONObject intrinsic(Intrinsic i) {
        return i==null?NavigationRecording.j():NavigationRecording.j("width",i.getWidth(),"height",i.getHeight(),
            "fx",i.getFx(),"fy",i.getFy(),"ppx",i.getPpx(),"ppy",i.getPpy(),"distortion",i.getModel().name(),"coefficients",NavigationRecording.array(i.getCoeffs()));
    }
}
