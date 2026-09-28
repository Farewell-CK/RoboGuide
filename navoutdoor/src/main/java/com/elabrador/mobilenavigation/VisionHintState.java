package com.elabrador.mobilenavigation;

import java.util.*;

/** Keeps the five primary direction rows separate from diagnostic state. */
final class VisionHintState {
    static final long MAX_AGE_MS=5000;
    private final Map<String,LinkedHashSet<String>> sectors=new LinkedHashMap<>();
    private long lastCapture=-1,latency;
    private String diagnostic="等待彩色画面";
    private VisionHintSnapshot snapshot=VisionHintSnapshot.EMPTY;

    VisionHintState(){reset("等待彩色画面");}

    void reset(String diagnostic){
        sectors.clear();
        for(String sector:VisionHintProtocol.SECTORS.keySet())sectors.put(sector,new LinkedHashSet<>());
        lastCapture=-1;latency=0;this.diagnostic=diagnostic;snapshot=VisionHintSnapshot.EMPTY;
    }

    void status(String diagnostic){this.diagnostic=diagnostic;}

    boolean accept(VisionHintProtocol.Result result,long capture,long now,long latency){
        if(capture<=lastCapture)return false;
        if(now-capture<0||now-capture>=MAX_AGE_MS){
            diagnostic="返回结果已过期，主提示保持上次结果";return false;
        }
        if(!result.usable){
            diagnostic="当前画面无法可靠判断，主提示保持上次结果";return false;
        }
        Map<String,LinkedHashSet<String>> replacement=new LinkedHashMap<>();
        for(String sector:VisionHintProtocol.SECTORS.keySet())replacement.put(sector,new LinkedHashSet<>());
        for(Map.Entry<String,String> entry:result.objects.entrySet()){
            int split=entry.getKey().indexOf(':');
            String sector=split<0?entry.getKey():entry.getKey().substring(0,split);
            int labelSplit=entry.getValue().indexOf('：');
            String type=labelSplit<0?"其他实体":entry.getValue().substring(labelSplit+1);
            LinkedHashSet<String> objects=replacement.get(sector);
            if(objects!=null)objects.add(type);
        }
        sectors.clear();sectors.putAll(replacement);
        lastCapture=capture;this.latency=latency;diagnostic="识别结果已更新";
        snapshot=new VisionHintSnapshot(capture,sectors);return true;
    }

    VisionHintSnapshot snapshot(){return snapshot;}

    String primaryText(){
        List<String> lines=new ArrayList<>();
        for(Map.Entry<String,String> sector:VisionHintProtocol.SECTORS.entrySet()){
            Set<String> objects=sectors.get(sector.getKey());
            lines.add(sector.getValue()+"："+(objects==null||objects.isEmpty()?"无":String.join("、",objects)));
        }
        return String.join("\n",lines);
    }

    String diagnosticText(long now,boolean analyzing){
        StringBuilder text=new StringBuilder(diagnostic);
        if(analyzing)text.append("\n正在分析最新片段……");
        if(lastCapture>=0){
            long age=Math.max(0,now-lastCapture);
            text.append(String.format(Locale.CHINA,"\n最后更新 %.1f秒前 · 请求耗时 %.1f秒",age/1000.,latency/1000.));
            if(age>=MAX_AGE_MS)text.append("\n识别结果超过5秒未更新，主提示为上次结果");
        }
        return text.toString();
    }
}
