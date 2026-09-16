package com.elabrador.mobilenavigation;

import java.util.*;

/** Final text for the external speech reader. Never feeds cloud results to the planner. */
final class GuidanceTextComposer {
    private static final long OBJECT_CHANGE_MILLIS=3000;
    private final Map<String,Integer> missing=new LinkedHashMap<>();
    private final Map<String,Long> seenAt=new LinkedHashMap<>();
    private long lastCapture=-1,changedAt;
    private String direction="",displayed="";

    String update(String stableDirection,VisionHintSnapshot snapshot,long now){
        if(stableDirection==null||stableDirection.isEmpty()){
            reset();return "";
        }
        if(snapshot==null)snapshot=VisionHintSnapshot.EMPTY;
        if(snapshot==VisionHintSnapshot.EMPTY){missing.clear();seenAt.clear();lastCapture=-1;}
        if(snapshot.fresh(now)&&snapshot.captureMillis>lastCapture){
            Set<String> observed=new LinkedHashSet<>();
            for(Map.Entry<String,Set<String>> entry:snapshot.objects.entrySet()){
                for(String type:entry.getValue()){
                    if(!VisionHintProtocol.TYPES.contains(type)||"其他实体".equals(type))continue;
                    observed.add(entry.getKey()+":"+canonical(type));
                }
            }
            for(String key:new ArrayList<>(missing.keySet())){
                if(!observed.contains(key)){
                    int count=missing.get(key)+1;
                    if(count>=2){missing.remove(key);seenAt.remove(key);}else missing.put(key,count);
                }
            }
            for(String key:observed){missing.put(key,0);seenAt.put(key,snapshot.captureMillis);}
            lastCapture=snapshot.captureMillis;
        }
        boolean directionChanged=!stableDirection.equals(direction);
        // A failed request/redraw is not a new scene. Do not retrigger speech for it.
        if(!directionChanged && snapshot!=VisionHintSnapshot.EMPTY && !snapshot.fresh(now))return displayed;
        String suffix=describe(now);
        String next=stableDirection.replace(" ","").replace("°","度")+(suffix.isEmpty()?"":"，"+suffix);
        if(directionChanged || (now-changedAt>=OBJECT_CHANGE_MILLIS&&!next.equals(displayed))){
            direction=stableDirection;displayed=next;changedAt=now;
        }
        return displayed;
    }

    private String describe(long now){
        Map<String,List<String>> grouped=new LinkedHashMap<>();
        for(Map.Entry<String,String> sector:VisionHintProtocol.SECTORS.entrySet()){
            Set<String> types=new TreeSet<>();
            for(Map.Entry<String,Long> seen:seenAt.entrySet()){
                if(seen.getKey().startsWith(sector.getKey()+":") && now>=seen.getValue()
                        && now-seen.getValue()<VisionHintState.MAX_AGE_MS)
                    types.add(seen.getKey().substring(seen.getKey().indexOf(':')+1));
            }
            if(!types.isEmpty())grouped.computeIfAbsent(String.join("、",types),k->new ArrayList<>()).add(sector.getValue());
        }
        List<String> clauses=new ArrayList<>();
        for(Map.Entry<String,List<String>> entry:grouped.entrySet())
            clauses.add(String.join("、",entry.getValue())+"有"+entry.getKey());
        return String.join("，",clauses);
    }

    private static String canonical(String type){
        if("人".equals(type))return "行人";
        if("建筑".equals(type))return "建筑物";
        if("墙体".equals(type))return "墙";
        if("柱子".equals(type))return "立柱";
        if("树".equals(type))return "树木";
        return type;
    }
    void reset(){direction="";displayed="";lastCapture=-1;changedAt=0;missing.clear();seenAt.clear();}
}
