package com.elabrador.mobilenavigation;

/** Select only a real, recent pose for displaying an already fused world map. */
final class SourceProjectionPose {
    private SourceProjectionPose() {}
    static VinsMono.Pose select(VinsMono.Pose capture,VinsMono.Pose current,
                                long currentPoseAgeMillis,boolean sameGeneration) {
        if(!sameGeneration || current==null || !current.initialized || capture==null
                || currentPoseAgeMillis<0 || currentPoseAgeMillis>=FrameEvidence.MAX_AGE_MILLIS
                || !Double.isFinite(current.timestamp) || current.timestamp<capture.timestamp
                || !Double.isFinite(current.x) || !Double.isFinite(current.y)
                || !Double.isFinite(current.z) || !Float.isFinite(current.egoRightAxisYawRadians()))return capture;
        return current;
    }
}
