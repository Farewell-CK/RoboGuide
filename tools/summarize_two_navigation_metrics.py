"""Report only per-frame Swin inference duration and actual new-map draw intervals."""
import argparse
import json
import math
import re
from pathlib import Path

from measure_ppt_metrics import distinct, matching_draws, number, stats


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    directory=args.directory
    metrics=json.loads((directory/'metrics.json').read_text(encoding='utf-8-sig'))
    events=json.loads((directory/'events.json').read_text(encoding='utf-8-sig'))
    manifest=metrics['recording']
    assert manifest.get('measurement_complete'), 'Collection did not complete'
    assert manifest['measurement_elapsed_host_seconds']>=240, 'Full 240-second run required'
    inferences=distinct(events['SWIN_INFERENCE_AUDIT'])
    model_values=[number(r.get('model_ms')) for r in inferences]
    assert model_values and all(v is not None and v>=0 for v in model_values)
    maps=distinct(events['FRAME_AUDIT'])
    draws,unmatched=matching_draws(events['MAP_DRAW_AUDIT'],maps)
    intervals=[]
    for previous,current in zip(draws,draws[1:]):
        delta=current['wall_ms']-previous['wall_ms']
        assert delta>0, 'Non-monotonic draw wall clock'
        intervals.append(delta)
    assert intervals, 'No consecutive physical new-map drawings observed'
    model=stats(model_values)
    assert math.isclose(model['mean'],metrics['semantic_model_ms']['all']['mean'],abs_tol=1e-9)
    refresh=stats(intervals)
    package=(directory/'package.txt').read_text(encoding='utf-8',errors='replace')
    version=re.search(r'versionName=([^\s]+)',package).group(1)
    result=dict(version=version,measurement_complete=True,
                measured_seconds=manifest['measurement_elapsed_host_seconds'],
                inference_duration_ms=model,new_map_refresh_interval_ms=refresh,
                completed_maps=len(maps),matched_draws=len(draws),unmatched_draw_events=len(unmatched),
                new_map_hz=1000/refresh['mean'],no_outliers_removed=True,
                cache_state=metrics['cache_state_claim'],cold_compile_calls=metrics['cold_compile_events_measurement'],
                scope='Physical RGB-D navigation; distinct semantic identities and actual Canvas callbacks. All consecutive draw gaps retained, including across generation changes.')
    (directory/'TWO_METRICS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Swin-L重新实测：推理耗时与地图刷新间隔','',
           f"版本：{version}；完整采集 {result['measured_seconds']:.2f} 秒；真实 D455f 导航，保留已有编译缓存。",'',
           '| 指标 | 样本数 | 平均数（秒） | 中位数（秒） |', '|---|---:|---:|---:|',
           f"| Swin-L单帧推理耗时 | {model['n']} | {model['mean']/1000:.3f} | {model['median']/1000:.3f} |",
           f"| 新地图刷新间隔 | {refresh['n']} | {refresh['mean']/1000:.3f} | {refresh['median']/1000:.3f} |",'',
           f"完成语义地图 {len(maps)} 张，实际匹配新图绘制 {len(draws)} 次，得到 {len(intervals)} 个相邻刷新间隔。窗口边界会使推理、建图和绘制的数量不同。",'',
           '单帧推理耗时：整网NPU运行器调用，包含主机提交，排除输入预处理、输出解码和建图。',
           '新地图刷新间隔：下一张不同语义地图首次绘制时刻减上一张首次绘制时刻；同一语义帧重复重绘不计数。实际新图之间的所有间隔均保留，不删除慢帧。','',
           f"按平均刷新间隔换算为 {result['new_map_hz']:.4f} Hz。未匹配绘图事件 {len(unmatched)} 次，未冒充新语义帧。",'',
           '原始证据：live.log、events.json、measurement.json。此表仅列本次要求的两个指标；统计原值以TWO_METRICS.json为准。']
    (directory/'TWO_METRICS.zh-CN.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
