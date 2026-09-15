package com.elabrador.mobilenavigation;

import org.json.*;
import java.util.*;

/** UI-only protocol; deliberately has no planner/map/pose dependencies. */
final class VisionHintProtocol {
    static final String PROMPT="你是独立的摄像头场景提示模块，不是导航控制器。图片按从旧到新排列，判断最后一帧当前仍可见的实体障碍；旧帧只供理解运动。"
            +"只描述画面内，不推测画面外或后方，不输出直走、转弯、停止等行动指令，不推断米制距离或保证通行安全。"
            +"将画面横向五等分为left/front_left/front/front_right/right，这是画面区域不是精确地理方位。"
            +"识别墙、建筑、玻璃护栏、车辆、行人、桌椅、箱子、立柱等实体。地面、阴影、道路纹理本身不算障碍。"
            +"同一物体可占多个区域。画面模糊、遮挡或不能判断时scene_usable=false，不能当作没有障碍。"
            +"directions只列有实体障碍的区域；没有列出的区域由界面显示为无。"
            +"严格输出JSON：{\"scene_usable\":true,\"obstacle_present\":true,\"directions\":[{\"sector\":\"front\",\"type\":\"行人\"}]}。"
            +"无明显障碍则obstacle_present=false,directions=[]。type只写简短中文物体名称。";
    static final Map<String,String> SECTORS=new LinkedHashMap<>();
    static {SECTORS.put("left","左侧");SECTORS.put("front_left","左前方");SECTORS.put("front","正前方");
        SECTORS.put("front_right","右前方");SECTORS.put("right","右侧");}
    static final Set<String> TYPES=new HashSet<>(Arrays.asList("墙","墙体","建筑","建筑物","玻璃","玻璃墙","玻璃护栏",
            "护栏","围栏","车辆","汽车","卡车","公交车","自行车","电动车","摩托车","行人","人","桌椅","桌子","椅子",
            "箱子","立柱","柱子","路障","树木","树","柜子","台阶","其他实体"));
    static final class Result {
        final boolean usable;final Map<String,String> objects;
        Result(boolean usable,Map<String,String> objects){this.usable=usable;this.objects=Collections.unmodifiableMap(objects);}
    }
    static Result parse(String body)throws JSONException {
        JSONObject envelope=new JSONObject(body);
        JSONArray choices=envelope.getJSONArray("choices");
        JSONObject choice=choices.getJSONObject(0);
        if(!"stop".equals(choice.optString("finish_reason")))throw new JSONException("incomplete response");
        String content=choice.getJSONObject("message").getString("content").trim();
        if(content.startsWith("```"))content=content.replaceFirst("^```(?:json)?\\s*","").replaceFirst("\\s*```$","").trim();
        JSONObject value=new JSONObject(content);
        Object usable=value.get("scene_usable"),present=value.get("obstacle_present");
        if(!(usable instanceof Boolean)||!(present instanceof Boolean))throw new JSONException("boolean required");
        JSONArray items=value.getJSONArray("directions");
        if(items.length()>25)throw new JSONException("too many objects");
        Map<String,String> objects=new LinkedHashMap<>();
        for(int i=0;i<items.length();i++){
            JSONObject item=items.getJSONObject(i);String sector=item.getString("sector"),type=item.getString("type").trim();
            if(!SECTORS.containsKey(sector)||type.isEmpty())throw new JSONException("invalid direction");
            if(!TYPES.contains(type))type="其他实体"; // Never show arbitrary model prose as a command.
            objects.put(sector+":"+type,SECTORS.get(sector)+"："+type);
        }
        if((Boolean)present != !objects.isEmpty())throw new JSONException("inconsistent presence");
        return new Result((Boolean)usable,objects);
    }
}
