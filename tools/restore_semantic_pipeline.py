"""Remove the abandoned geometric fast-map experiment; keep a local review snapshot."""
from pathlib import Path
import shutil
root=Path(__file__).resolve().parents[1]
java=root/'navoutdoor/src/main/java/com/elabrador/mobilenavigation'
archive=root/'artifacts/d455f-device-20261104/abandoned-geometric-map'
archive.mkdir(exist_ok=True)
for name in ['SemanticSegmenter.java','OutdoorNavController.kt']:
    shutil.copy2(java/name,archive/name)
for name in ['LiveDepthMap.java','WorldObstacleMemory.java','DepthPoseQueue.java']:
    p=java/name
    if p.exists():shutil.move(p,archive/name)
test=root/'navoutdoor/src/test/java/com/elabrador/mobilenavigation'
for name in ['LiveDepthMapTest.java','LiveDepthGeometryTest.java','LiveDepthDeviceReplayTest.java',
             'WorldObstacleMemoryTest.java','WorldObstacleReplayTest.java','OverredDeviceReplayTest.java','DepthPoseQueueTest.java']:
    p=test/name
    if p.exists():shutil.move(p,archive/name)
p=java/'SemanticSegmenter.java';s=p.read_text(encoding='utf-8')
start=s.index('    private final ExecutorService depthExecutor=')
end=s.index('    void updateVinsPose(',start)
s=s[:start]+'''    boolean isCurrentEvidence(FrameEvidence evidence) {
        return !closed && evidence!=null && evidence.generation==vinsGeneration.get();
    }

'''+s[end:]
s=s.replace('            if(USE_SWINL) scheduleDepth();\n','')
s=s.replace('        synchronized (depthLock) { depthQueue.clear(); }\n','')
s=s.replace('        obstacleMemory.clear();\n','').replace('        depthExecutor.shutdownNow();\n','')
p.write_text(s,encoding='utf-8')
p=java/'OutdoorNavController.kt';s=p.read_text(encoding='utf-8')
s=s.replace('private val LIVE_DEPTH =','private val SOURCE_SWINL =')
s=s.replace('    private val pendingLiveDepthMap = AtomicReference<LiveDepthMap?>()\n','')
s=s.replace('        val evidence: FrameEvidence?,\n        val liveMap: LiveDepthMap? = null','        val evidence: FrameEvidence?')
s=s.replace('if (navigationActive && !LIVE_DEPTH)','if (navigationActive)')
s=s.replace('(!LIVE_DEPTH || navigationFrame)','(!SOURCE_SWINL || navigationFrame)')
start=s.index('            if (LIVE_DEPTH && navigationActive')
end=s.index('            }\n',s.index('                }\n',start))+14
s=s[:start]+s[end:]
s=s.replace('        pendingLiveDepthMap.set(null)\n','')
s=s.replace('        val liveReason = published.liveMap?.waitingReason(cuePlan, now)\n','')
s=s.replace('evidence.fresh(now) && liveReason == null &&','evidence.fresh(now) &&')
s=s.replace('(!LIVE_DEPTH || semanticSegmenter?.isCurrentEvidence(evidence) == true)',
            'semanticSegmenter?.isCurrentEvidence(evidence) == true')
s=s.replace('                liveReason != null -> liveReason\n','')
s=s.replace('if (LIVE_DEPTH) "等待新鲜深度地图" else "等待新鲜语义地图"','"等待新鲜语义地图"')
s=s.replace('liveReason != null || cueEvidence?.fresh(now) == false','cueEvidence?.fresh(now) == false')
s=s.replace('if (LIVE_DEPTH) refreshLiveDepthPlan() else refreshLocalPlanOnce(segmenter)',
            'refreshLocalPlanOnce(segmenter)')
start=s.index('    private fun refreshLiveDepthPlan()')
end=s.index('    private fun refreshLocalPlanOnce(',start)
s=s[:start]+s[end:]
s=s.replace('if (LIVE_DEPTH) {','if (SOURCE_SWINL) {')
s=s.replace('实时深度地图负责当前障碍检查','地图仅随新的 Swin-L 语义结果更新')
s=s.replace('if (previousNanos > 0L) renderedNanos - previousNanos else -1L','if (previousNanos > 0L) renderedNanos - previousNanos else -1L')
s=s.replace('val projected = requestedMap ?: segmenter.reprojectLatestLocalMap()',
            'val projected = requestedMap ?: if (SOURCE_SWINL) return else segmenter.reprojectLatestLocalMap()')
p.write_text(s,encoding='utf-8')
print('Restored semantic result driven controller; geometric experiment archived at',archive)
