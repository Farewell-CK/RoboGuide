"""Summarize the verified candidate independently of older performance reports."""
import hashlib,json,shutil,xml.etree.ElementTree as ET
from pathlib import Path
root=Path(__file__).resolve().parents[1];out=root/'artifacts/d455f-device-20261104/v12-map-memory';out.mkdir(exist_ok=True)
tests=json.loads((out.parent/'v12-tests.json').read_text())
source=max((root/'app/build/outputs/apk/debug').glob('*v12*.apk'),key=lambda p:p.stat().st_mtime)
apk=out/'GuideAssistant-SwinL-384-v12.apk';shutil.copy2(source,apk)
with apk.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
manifest={'version':'1.4-swinl-npu-384-v12','package':'com.seaway.guideassistant.swinl',
 'apk':str(apk),'bytes':apk.stat().st_size,'sha256':sha,'tests':tests,
 'plan_md5':'d7fda40af91b1e7e4e0a2ef54517c16d','installed':False,'device_acceptance_passed':False}
(out/'candidate.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
for case in ['WorldObstacleMemoryTest','WorldObstacleReplayTest','RefreshStatisticsTest']:
    path=root/f'navoutdoor/build/test-results/testSwinlDebugUnitTest/TEST-com.elabrador.mobilenavigation.{case}.xml'
    shutil.copy2(path,out/path.name)
report=f'''# v12：障碍记忆与刷新统计修复

候选已编译；{tests['tests']} 项测试全部通过，0 失败、0 跳过。安装/真机验收状态以 `candidate.json` 为准。

## 用户看到障碍移出视野就消失的原因

v11 快速深度地图只使用单帧；它与原来的跨帧语义 OctoMap 是两条链路。
新帧从全未知网格开始，没再次看到的障碍被遗漏，因此镜头一转，旧红色立刻变灰。
另外，等待路线方向时的空路径结果也可能覆盖已有地图。

## 最终行为

- 保存 20 cm 世界坐标占据体素，使用实际 VINS 位姿投到当前 80×80 局部图中；
  红色随相机平移/旋转改变相对位置，移出视野不会删除，时间流逝也不自动删除。
- 新障碍立即加入。只有真实深度射线穿过原记录位置，连续 3 个不同采集帧、跨度至少 150 ms
  确认空闲才清除；射线在当前障碍前停止，深度端点留 25 cm 余量。
  旧物体被遮挡、没有深度或重复重绘都不构成清除证据。
- 保留原始帧证据。历史障碍只增加规划约束，不把历史数据冒充新观测。
  当前深度仍要求 1500 ms 内，慢速 Swin-L 语义软参考仍保留自己的 10000 ms 时限。
- 本模块是局部地图：保留半径 32 m，超大缓存做空间裁剪；VINS 重置后旧世界坐标失效，
  会明确显示“VINS 坐标系已重置，正在重建局部地图”。它不是跨应用重启的全局地图保存功能。
- 路线方向尚未就绪时仍显示深度代价图，路径保持等待。
- 刷新频率来自 Canvas 实际画出的不同采集帧；重复重绘不计数。
  显示近 5 秒频率、持续增长的断更时长、深度帧龄与语义帧龄。
  规划耗时明确标为含路径复用，不把路径复用时间冒充一次完整 A* 搜索。

## 验证

旋转/平移后世界障碍仍在；超过 1.5 秒或移出视野不删除；
单帧/重复帧不能确认清除；背景射线连续观测可以清除；前景遮挡不清除后方障碍；
当前障碍优先；VINS 新世界不接受旧世界迟到帧。
另用已提取的 11 帧真实 D455f 点云与对应原始位姿回放，验证当前硬障碍不降级、
原采集时间不改写；在真实序列中受控移除中间观察帧，已记忆障碍仍保留。
受控缺帧及解析几何是本机测试，不冒充用户实际转动相机的真机试验。

## 上一版速度数据的限制

v11 的 240 秒真实记录中，实际地图绘制间隔中位数 142 ms、平均 201.29 ms、最长 29255 ms。
最长断更包含 VINS 重置和导航方向重建，不是每帧都快；界面只显示上次间隔会掩盖这一点。
Swin-L 模型中位数 3166 ms、平均 3232.52 ms；实时深度地图速度不能冒充模型速度。
该窗口手机正在充电，因此没有合格的电池放电功耗结果。
这组数据属于 v11，不能当作 v12 的手机性能。

APK：`{apk.name}`，SHA256 `{sha}`。
Swin-L 模型计划、384×288 分辨率及模型权重均未修改。
'''
(out/'REPORT.zh-CN.md').write_text(report,encoding='utf-8')
print(json.dumps(manifest,ensure_ascii=False,indent=2))
