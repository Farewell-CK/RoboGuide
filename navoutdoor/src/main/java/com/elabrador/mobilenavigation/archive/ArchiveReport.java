package com.elabrador.mobilenavigation.archive;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Bounded streaming report from durable artifacts; absence is never inferred to mean success. */
final class ArchiveReport {
    static final String README="README.zh-CN.md", SUMMARY="capture-summary.json", DICTIONARY="data-dictionary.json";
    static void generate(File directory,JSONObject manifest)throws Exception{
        JSONObject counts=new JSONObject(),bytes=new JSONObject(),eventCounts=new JSONObject();
        JSONArray files=manifest.getJSONArray("files"),issues=new JSONArray(),pending=new JSONArray(),annotations=new JSONArray();
        boolean vinsReady=false;long maxIssueDetails=100;JSONObject start=new JSONObject();
        for(int i=0;i<files.length();i++){
            JSONObject file=files.getJSONObject(i);String type=file.optString("type");
            if(isReportType(type))continue;
            counts.put(type,counts.optLong(type)+1);bytes.put(type,bytes.optLong(type)+file.optLong("size"));
            if("experiment_annotation".equals(type))annotations.put(ArchiveStore.readJson(ArchiveStore.checkedFile(directory,file.getString("path"))));
            if(!"events".equals(type)&&!file.optString("path").endsWith(".jsonl"))continue;
            try(BufferedReader input=new BufferedReader(new InputStreamReader(new FileInputStream(ArchiveStore.checkedFile(directory,file.getString("path"))),StandardCharsets.UTF_8))){
                String line;while((line=input.readLine())!=null){
                    JSONObject event;
                    try{event=new JSONObject(line);}catch(Exception invalid){eventCounts.put("unreadable_event_lines",eventCounts.optInt("unreadable_event_lines")+1);continue;}
                    String kind=event.optString("type");JSONObject data=event.optJSONObject("data");if(data==null)data=new JSONObject();
                    eventCounts.put(kind,eventCounts.optLong(kind)+1);
                    if("vins_pose".equals(kind)&&data.optJSONObject("pose")!=null)vinsReady|=data.getJSONObject("pose").optBoolean("initialized");
                    if("navigation.start_requested".equals(kind)||"navigation.requested".equals(kind)||("navigation.started".equals(kind)&&start.length()==0))start=event;
                    if("processing_closed_before_result".equals(kind)&&pending.length()<100)pending.put(event);
                    if((kind.contains("failed")||kind.contains("failure")||kind.contains("error")||kind.contains("dropped")||kind.contains("gap")||kind.contains("unavailable"))&&issues.length()<maxIssueDetails)issues.put(event);
                }
            }
        }
        JSONObject modules=new JSONObject();
        modules.put("video",module(counts,"navigation_video","recorded","no_video_artifact"));
        modules.put("vins",ArchiveStore.json("status",vinsReady?"initialized":"not_observed_initialized","pose_events",eventCounts.optLong("vins_pose"),"features_files",counts.optLong("vins_features")));
        modules.put("semantic",ArchiveStore.json("status",counts.optLong("semantic_labels")>0?"results_recorded":!vinsReady?"no_results_vins_not_ready_or_not_observed":"no_results_observed","result_files",counts.optLong("semantic_labels"),"started_inputs",counts.optLong("inference_rgb")));
        modules.put("octomap",ArchiveStore.json("status",counts.optLong("octomap_full_checkpoint")>0?"full_snapshots_recorded":counts.optLong("semantic_labels")==0?"no_semantic_result_observed":"no_full_snapshot_check_issues","checkpoints",counts.optLong("octomap_full_checkpoint"),"deltas",counts.optLong("octomap_full_delta"),"final_snapshots",counts.optLong("octomap_full_final_snapshot")));
        modules.put("cloud_visual",ArchiveStore.json("status",eventCounts.optLong("cloud.visual.request")>0?"requests_observed":"no_request_observed","requests",eventCounts.optLong("cloud.visual.request"),"responses",eventCounts.optLong("cloud.visual.response"),"results",eventCounts.optLong("cloud.visual.result")));
        modules.put("location",ArchiveStore.json("status",eventCounts.optLong("location")>0?"fixes_observed":"no_location_event","events",eventCounts.optLong("location")));
        JSONObject summary=ArchiveStore.json("schema_version",1,"session_id",manifest.optString("session_id"),"default_experiment_id",manifest.optString("session_id"),
            "state",manifest.optString("state"),"capture_complete",manifest.optBoolean("capture_complete"),"started_at_ms",manifest.opt("started_at_ms"),"ended_at_ms",manifest.opt("ended_at_ms"),
            "end_reason",manifest.opt("end_reason"),"file_counts_by_type",counts,"file_bytes_by_type",bytes,"event_counts_by_type",eventCounts,"modules",modules,"last_navigation_start_event",start,
            "dropped_records",manifest.optLong("dropped_records"),"write_errors",manifest.optLong("write_errors"),"missing_required_types",manifest.optJSONArray("missing_required_types"),
            "issue_details_first_100",issues,"unfinished_at_end_first_100",pending,"experiment_annotations",annotations,
            "report_scope","Generated from durable manifest files; zero files can mean not generated, not ready, or missing. Review issue events; no fabricated results. Reports themselves excluded from file counts.",
            "annotations_note","Append-only files in artifacts/experiment_annotation are authoritative; later annotations can postdate this seal-time report.");
        ArchiveStore.atomicJson(new File(directory,SUMMARY),summary);
        ArchiveStore.atomicJson(new File(directory,DICTIONARY),dictionary());
        write(new File(directory,README),readme(manifest,summary));
    }
    static boolean isReportType(String type){return type.equals("archive_readme")||type.equals("capture_summary")||type.equals("data_dictionary");}
    private static JSONObject module(JSONObject counts,String type,String yes,String no){return ArchiveStore.json("status",counts.optLong(type)>0?yes:no,"files",counts.optLong(type));}
    private static void write(File file,String value)throws Exception{
        File partial=new File(file+".tmp");try(FileOutputStream output=new FileOutputStream(partial)){output.write(value.getBytes(StandardCharsets.UTF_8));output.getFD().sync();}
        if(!partial.renameTo(file))throw new IOException("Could not finalize archive explanation");
    }
    static JSONObject dictionary(){
        JSONObject d=new JSONObject();
        entry(d,"runtime_configuration","JSON：构建标识、设备/Android、模型、相机固件/流/标定、VINS与规划配置；未知值明确标注。", "见字段单位；旋转矩阵注明行列顺序");
        entry(d,"semantic_configuration","JSON：类别、颜色与规划代价配置。","类别ID及RGB语义颜色");
        entry(d,"events","UTF-8 JSONL：每行一个事件；artifact事件保存每个文件的完整metadata与关联编号。","wall_time_ms为Unix毫秒；elapsed_time_ns为Android单调时钟纳秒；两者不能混算");
        entry(d,"navigation_video","H.264 MP4，640×480目标15FPS，约30秒一段，无音频。逐帧索引在artifact事件metadata.frames。","PTS微秒；索引提供采集时间与帧号");
        entry(d,"inference_rgb","JPEG质量80；只保存实际执行的语义推理输入。","640×480 RGB；用inference_id关联深度及结果");
        entry(d,"inference_raw_depth","gzip解压后小端uint16 Z16，含行stride，未对齐深度。","米=uint16×depth_unit_m；0无效；按原始深度相机内参解释");
        entry(d,"inference_aligned_depth","gzip解压后小端uint16 Z16，librealsense Align(COLOR)。","米=uint16×depth_unit_m；0无效；使用彩色内参");
        entry(d,"semantic_labels","uint8逐像素类别ID，row-major；Swin-L 384×288。","类别与颜色映射见semantic_configuration");
        entry(d,"semantic_confidence","小端float32逐像素置信度，row-major。","模型输出置信度；并非地图地面增强置信度");
        entry(d,"semantic_point_cloud","小端重复20字节：[float32 x,y,z,uint32 semantic_rgb,float32 confidence]；每10秒保存新结果。","相机光学坐标：x右/y下/z前，米；包含地面");
        entry(d,"octomap_full_checkpoint",".navmap为STORED ZIP：occupancy.ot和semantics.bin（NSEM0001）。","世界坐标，米；完整当前原生树；含空闲/占用和完整语义字段，普通.ot不能代替整个包");
        entry(d,"octomap_full_delta","gzip后NDT1无损字节差分；大端magic、目标长度i64、重复(offset i64,length i32,bytes)，offset=-1结束。","按generation/sequence恢复，同基线SHA校验；不是直接体素删除列表");
        entry(d,"octomap_full_final_snapshot",".navmap完整结束快照；导航地图本身会裁掉远处历史。","整段轨迹需结合沿途快照/差分；不跨generation合并");
        entry(d,"octomap_local_checkpoint","小端float32×8：x,y,z,occupancy,r,g,b,semantic_confidence。","仅局部占用叶子；世界坐标米，不代表完整树");
        entry(d,"octomap_local_final_snapshot","与局部检查点相同。","局部范围，仅用于显示/分析");
        entry(d,"octomap_local_delta","小端u32 upsert数量/u32 scope_exit数量；upserts float32×8、exits float32×3。","scope_exit表示不再出现在局部导出范围，不证明原树物理删除");
        entry(d,"local_cost_grid","小端int32，80×80 row-major，分辨率0.2m，原点(-7.9,-7.9)。","自车坐标x右/y前；未知格值按metadata/配置解释");
        entry(d,"display_cost_grid","显示用代价栅格，编码同local_cost_grid。","自车坐标x右/y前，米");
        entry(d,"d455_imu","小端44字节：i32 stream,f64 raw_ms,f64 unix_ms,i64 elapsed_ns,f32 x/y/z,i32 domain。","1陀螺rad/s，2加速度m/s²；domain:0 hardware/1 system/2 global/-1 unknown");
        entry(d,"vins_features","小端重复帧头f64 timestamp_s,i64 generation,i32 count，后接count×float64[8]。","字段id,x,y,z,u,v,velocity_x,velocity_y；时间秒；VINS特征不等于稠密点云");
        entry(d,"cloud.visual.image","实际提交千问的JPEG质量70，按image_id去重。","640×480；请求事件按ID引用，不重复保存Base64");
        entry(d,"experiment_annotation","UTF-8 JSON，追加不可变版本；experiment_id/scene_notes/arrival_confirmation/manual_takeover/failure_reason/notes/relative_seconds。","人工确认与算法到达事件分开；unconfirmed/空值表示未知；录入时间不冒充事件发生时间");
        return ArchiveStore.json("schema_version",1,"byte_order","binary fields use indicated endian","types",d,"association","inference_id / image_id / request_id / generation / sequence；详细metadata在artifact事件","coordinates","VINS世界为本次generation局部世界；GPS WGS84，高德路线GCJ02；必须先转换再比较");
    }
    private static void entry(JSONObject d,String type,String format,String unit){try{d.put(type,ArchiveStore.json("format",format,"units_and_coordinates",unit));}catch(Exception impossible){throw new IllegalStateException(impossible);}}
    private static String readme(JSONObject m,JSONObject summary){
        StringBuilder out=new StringBuilder("# 导航实验数据包\n\n");
        out.append("默认实验编号：").append(m.optString("session_id")).append("\n\n");
        out.append("先看 capture-summary.json 了解采集状态，再看 data-dictionary.json 了解文件格式、单位和坐标系。manifest.json 是文件清单，含大小与SHA-256。所有清单文件一起保留。\n\n");
        out.append("runtime_configuration 中保存运行时配置。events-*.jsonl 是过程事件，每行独立JSON；artifact事件的metadata保留帧号、标定、索引和文件关联。\n\n");
        out.append("采集状态：").append(m.optString("state")).append("；写入错误：").append(m.optLong("write_errors")).append("；队列丢弃：").append(m.optLong("dropped_records")).append("。\n\n");
        out.append("没有语义或地图文件不自动意味着文件丢失：检查摘要的模块状态、是否完成VINS初始化，以及失败/缺失事件。结束时未完成的推理/云请求单独列出。录制完整只表示已观测数据写入成功，不表示所有算法均就绪。\n\n");
        out.append("人工标注在 artifacts/experiment_annotation/，按revision递增，最新版本为当前说明，历史版本保留。未确认到达/接管为未知，不能当成否。封存后补填的标注可能晚于这份摘要，务必检查此目录。\n\n");
        out.append("OctoMap：完整文件为.navmap，含occupancy.ot及semantics.bin；差分需按generation/sequence和SHA恢复。导航本身裁剪远处地图，结束包不能单独代表整段路线。工具：inspect_navigation_archive.py --reconstruct-maps。\n\n");
        out.append("视频/JPEG有损压缩；Z16深度gzip无损；不保存连续原始RGB/深度、音频、API Key或认证头。模型文件本体另行保留，包内用模型SHA标识。\n\n");
        out.append("## 文件数量（不含本说明与摘要/字典）\n\n");
        JSONObject counts=summary.optJSONObject("file_counts_by_type");if(counts!=null){java.util.Iterator<String> keys=counts.keys();while(keys.hasNext()){String k=keys.next();out.append("- ").append(k).append(": ").append(counts.optLong(k)).append("\n");}}
        return out.toString();
    }
}
