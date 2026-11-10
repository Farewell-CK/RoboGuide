"""Summarize a completed physical navigation run with both means and medians."""
import argparse
import csv
import json
from pathlib import Path

from measure_ppt_metrics import distinct, identity, number, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    directory = args.directory
    metrics = json.loads((directory/'metrics.json').read_text(encoding='utf-8-sig'))
    events = json.loads((directory/'events.json').read_text(encoding='utf-8-sig'))
    manifest = metrics['recording']
    if not manifest.get('measurement_complete'):
        raise SystemExit('Collection is incomplete; do not label this a full-duration result.')

    rows = []
    def add(label, key, group='all', unit='ms'):
        item = metrics[key].get(group)
        if item:
            rows.append(dict(label=label, metric=key, group=group, unit=unit, **item))
    add('Swin-L单帧推理耗时', 'semantic_model_ms')
    add('推理完成间隔', 'inference_completion_refresh', 'interval_ms')
    add('新语义地图完成间隔', 'semantic_refresh', 'interval_ms')
    add('地图UI更新间隔（PPT计时位置）', 'plan_ui_submission_refresh', 'interval_ms')
    add('新语义地图实际绘制间隔', 'semantic_map_new_draw_refresh', 'interval_ms')
    add('采集→地图完成延迟', 'semantic_capture_to_map_ms')
    add('采集→地图绘制延迟', 'capture_to_semantic_draw_ms')
    add('输入排队', 'input_queue_ms')
    add('RGB预处理', 'preprocess_ms')
    add('输出解码', 'decode_ms')
    add('语义点云', 'semantic_cloud_ms')
    add('OctoMap插入（含平面置信处理）', 'octomap_insert_ms')
    add('代价栅格', 'semantic_grid_ms')
    add('模型输出后的完整建图', 'semantic_map_ms')
    add('完整局部规划（含路径复用）', 'planning_attempt_ms')
    add('实际执行A*的规划调用', 'astar_when_searching_ms')
    add('规划→UI排队', 'plan_ui_queue_ms')

    inferences = distinct(events['SWIN_INFERENCE_AUDIT'])
    matched = []
    seen = set()
    for draw in events['MAP_DRAW_AUDIT']:
        draw_frame = number(draw.get('frame'))
        candidates = [r for r in inferences if draw_frame is not None
            and r.get('pid') == draw.get('pid')
            and r.get('generation') == draw.get('generation')
            and abs(number(r.get('frame'))-draw_frame) <= 1e-6]
        if len(candidates) != 1:
            continue
        inference = candidates[0]
        key = identity(inference)
        model = number(inference.get('model_ms'))
        end_to_end = number(draw.get('capture_to_draw_ms'))
        if key in seen or model is None or end_to_end is None or end_to_end < model:
            continue
        seen.add(key)
        matched.append(dict(pid=key[0], generation=key[1], frame=key[2],
            model_ms=model, capture_to_draw_ms=end_to_end, non_model_ms=end_to_end-model))
    residual = stats([r['non_model_ms'] for r in matched])
    if residual:
        rows.append(dict(label='同一帧端到端减推理的其余耗时', metric='matched_non_model_ms',
                         group='matched_pairs', unit='ms', **residual))
    result = dict(version='v15', measurement_seconds=manifest['measurement_elapsed_host_seconds'],
                  measurement_complete=True, rows=rows, matched_pairs=matched,
                  no_outliers_removed=True, input_injected=False,
                  map_refresh_hz=metrics['semantic_map_new_draw_refresh']['hz'],
                  duplicate_inference_events=metrics['duplicate_inference_events'],
                  duplicate_frame_events=metrics['duplicate_frame_events'],
                  unmatched_draw_events=metrics['unmatched_draw_events'],
                  semantic_map_drop_events=metrics['semantic_map_drop_events'],
                  fatal_error=metrics['fatal_error'])
    (directory/'mean-median.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (directory/'mean-median.csv').open('w',encoding='utf-8-sig',newline='') as output:
        writer=csv.DictWriter(output,fieldnames=['label','metric','group','unit','n','mean','median','min','max','p90'])
        writer.writeheader();writer.writerows(rows)

    text = ['# Swin-L v15 完整实测：平均数与中位数', '',
            f"完整采集 {manifest['measurement_elapsed_host_seconds']:.2f} 秒。所有有效样本参与统计，不删除慢帧，不用短日志代替完整窗口。", '',
            '使用真实D455f与现有导航、保留已有编译缓存；未重装、重启、注入图像/位姿或改变亮度。', '',
            '| 指标 | 样本数 | 平均数（秒） | 中位数（秒） | P90（秒） | 最大（秒） |',
            '|---|---:|---:|---:|---:|---:|']
    for row in rows[:7]:
        text.append(f"| {row['label']} | {row['n']} | {row['mean']/1000:.3f} | {row['median']/1000:.3f} | {row['p90']/1000:.3f} | {row['max']/1000:.3f} |")
    text += ['', '单帧推理耗时、相邻更新间隔、采集到绘制延迟分别统计；它们不能相加，也不能互换。UI更新间隔最接近旧PPT计时位置，实际Canvas首次绘制另外列出。窗口边界可使各指标样本数不同。', '',
             '| 其余阶段 | 样本数 | 平均数（ms） | 中位数（ms） |', '|---|---:|---:|---:|']
    for row in rows[7:]:
        text.append(f"| {row['label']} | {row['n']} | {row['mean']:.3f} | {row['median']:.3f} |")
    text += ['', '“其余耗时”先对同一真实帧做端到端减模型时间，再计算平均数/中位数；不是两个独立中位数相减。它包含输入准备/排队、解码、点云/OctoMap、规划和绘制等待。阶段日志可能包含重叠/整数舍入，不能把中位数直接相加。', '',
             f"匹配同帧对数：{len(matched)}；重复推理日志：{metrics['duplicate_inference_events']}；重复建图日志：{metrics['duplicate_frame_events']}；未匹配绘图日志：{metrics['unmatched_draw_events']}；语义地图丢弃日志：{metrics['semantic_map_drop_events']}。", '',
             f"实际新语义地图绘制频率：{metrics['semantic_map_new_draw_refresh']['hz']:.4f} Hz。这是新语义帧去重后的频率，不是界面重绘次数。", '',
             '本次没有同步标记一次转动的起点，不能把采集→绘制延迟当作转动→反应时间。原始证据：live.log、events.json、resources.jsonl、measurement.json。每帧配对和完整统计：mean-median.json；可用表格：mean-median.csv。']
    (directory/'MEAN_MEDIAN.zh-CN.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='matched_pairs'},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
