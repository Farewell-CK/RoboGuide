package com.elabrador.mobilenavigation;

import org.json.*;
import org.junit.Test;
import static org.junit.Assert.*;

public class VisionHintTest {
    private String response(boolean usable,boolean present,String items)throws Exception {
        JSONObject value=new JSONObject().put("scene_usable",usable).put("obstacle_present",present)
                .put("directions",new JSONArray(items));
        return new JSONObject().put("choices",new JSONArray().put(new JSONObject().put("finish_reason","stop")
                .put("message",new JSONObject().put("content",value.toString())))).toString();
    }
    private VisionHintProtocol.Result result(boolean usable,boolean present,String items)throws Exception {
        return VisionHintProtocol.parse(response(usable,present,items));
    }
    private VisionHintProtocol.Result obstacle()throws Exception {
        return result(true,true,"[{\"sector\":\"left\",\"type\":\"箱子\"}]");
    }
    private VisionHintProtocol.Result empty()throws Exception {return result(true,false,"[]");}

    @Test public void initialPrimaryAlwaysListsFiveDirectionsAsClear(){
        VisionHintState s=new VisionHintState();
        assertEquals("左侧：无\n左前方：无\n正前方：无\n右前方：无\n右侧：无",s.primaryText());
    }
    @Test public void validSnapshotReplacesAllFiveDirections()throws Exception {
        VisionHintState s=new VisionHintState();
        s.accept(result(true,true,"[{\"sector\":\"left\",\"type\":\"箱子\"},{\"sector\":\"front\",\"type\":\"行人\"}]"),1000,1100,100);
        assertEquals("左侧：箱子\n左前方：无\n正前方：行人\n右前方：无\n右侧：无",s.primaryText());
        s.accept(empty(),2000,2100,100);
        assertEquals("左侧：无\n左前方：无\n正前方：无\n右前方：无\n右侧：无",s.primaryText());
    }
    @Test public void multipleObjectsInOneDirectionAreCombined()throws Exception {
        VisionHintState s=new VisionHintState();
        s.accept(result(true,true,"[{\"sector\":\"right\",\"type\":\"车辆\"},{\"sector\":\"right\",\"type\":\"行人\"}]"),1000,1100,100);
        assertTrue(s.primaryText().contains("右侧：车辆、行人"));
    }
    @Test public void supportsDirectionsAndNeverDisplaysFreeFormModelCommands()throws Exception {
        assertEquals("左侧：箱子",obstacle().objects.get("left:箱子"));
        VisionHintProtocol.Result bad=result(true,true,"[{\"sector\":\"right\",\"type\":\"立即向左转\"}]");
        assertEquals("右侧：其他实体",bad.objects.get("right:其他实体"));
    }
    @Test public void rejectsContradictoryOrRearDirection()throws Exception {
        for(String body:new String[]{response(true,true,"[]"),response(true,true,"[{\"sector\":\"rear\",\"type\":\"墙\"}]")}){
            try{VisionHintProtocol.parse(body);fail();}catch(JSONException expected){}
        }
    }
    @Test public void staleUnusableAndNetworkStatusPreservePrimary()throws Exception {
        VisionHintState s=new VisionHintState();s.accept(obstacle(),1000,1100,100);
        assertFalse(s.accept(obstacle(),1100,6200,5100));
        assertTrue(s.primaryText().contains("左侧：箱子"));
        assertTrue(s.diagnosticText(6200,false).contains("过期"));
        s.accept(result(false,false,"[]"),2000,6300,100);
        assertTrue(s.primaryText().contains("左侧：箱子"));
        s.status("网络不可用，视觉提示暂停");
        assertTrue(s.primaryText().contains("左侧：箱子"));
        assertTrue(s.diagnosticText(6400,false).contains("网络不可用"));
    }
    @Test public void resetClearsPrimaryWhileStatusDoesNot()throws Exception {
        VisionHintState s=new VisionHintState();s.accept(obstacle(),1000,1100,100);
        s.status("后台暂停上传");assertTrue(s.primaryText().contains("箱子"));
        s.reset("已关闭");assertFalse(s.primaryText().contains("箱子"));
        assertTrue(s.diagnosticText(1200,false).contains("已关闭"));
    }
    @Test public void repeatedAndOutOfOrderResultsCannotOverwriteLatest()throws Exception {
        VisionHintState s=new VisionHintState();s.accept(obstacle(),2000,2100,100);
        assertFalse(s.accept(empty(),2000,2200,100));assertFalse(s.accept(empty(),1500,2300,100));
        assertTrue(s.primaryText().contains("箱子"));
    }
    @Test public void diagnosticsShowAnalysisAgeAndLatency()throws Exception {
        VisionHintState s=new VisionHintState();s.accept(obstacle(),1000,1100,100);
        String text=s.diagnosticText(2200,true);
        assertTrue(text.contains("正在分析最新片段"));
        assertTrue(text.contains("最后更新 1.2秒前"));
        assertTrue(text.contains("请求耗时 0.1秒"));
        assertTrue(s.diagnosticText(7000,false).contains("主提示为上次结果"));
    }
    @Test public void endpointsRequireTlsAndRejectEmbeddedCredentials(){
        assertEquals("https://example.com/v1/chat/completions",QwenVisionHints.normalizeEndpoint("https://example.com/v1/"));
        for(String url:new String[]{"http://example.com","https://key@example.com","https://example.com?key=abc"}){
            try{QwenVisionHints.normalizeEndpoint(url);fail();}catch(IllegalArgumentException expected){}
        }
    }
}
