#!/usr/bin/env python3
"""受控实验: 只放目标箱子(远离其它障碍), 单独检验"推偏 → 横向修正"。

要回答的问题(用户提出):
    初始就把车摆在"箱子后方但横向偏 d0"的位置,
    推偏修正应该侧挪多少? 是不是只需要修正一次?
理论答案(本脚本的第一段会打印):
    让接触点落在"箱子→目标"的连线上(横向 0), 需要的侧挪量 **就等于当前的横向偏差**,
    而且一次到位后只要箱子不再被顶偏, 后续不需要再次修正 —— 修正量必须由"当时的
    箱子-目标连线"算出, 不能事先定成某个固定值(如 0.15 m)。

实验做法:
    1. /v1/reset, 把 obs2 放到 B, 把车放到 "B 后方 + 横向 d0"
    2. 信念播种成 B(相当于一次准确测量), 然后调用局部推-看控制器
    3. 统计: 横向修正发生了几次、修正了多少、箱子最终离目标多远、偏离连线多远

注意: 世界只有一个, 跑这个实验前请先停掉批次(run 脚本)。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import math
import sys
import time
import types
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "robonix-src" / "examples" / "mujoco" / "facade" / "robonix_facade.py"
WORLD = "http://127.0.0.1:28100"

# 空旷区: 远离 obs1/3/4/5、marker、arm_base(最近的是 arm_base, 约 1.2 m)
BOX = (-1.90, 0.60)
TARGET = (-1.90, 1.70)
STANDOFF = 0.68
PUSH_LEN = 1.10          # 箱子要走的距离
PUSH_U = (1.0, 0.0)      # 推挤方向(世界 +x): 空旷走廊          # 车心到箱子中心: 自车支撑 0.28 + 目标 0.36 + 0.04


def _get(url: str):
    with urllib.request.urlopen(url, timeout=6) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(path: str, body: dict):
    request = urllib.request.Request(
        WORLD + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_module():
    spec = importlib.util.spec_from_file_location("facade_mod", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_facade(module):
    class Probe:
        role = "rover"
        world_url = WORLD
        _look_reason = ""

    probe = Probe()
    for name in dir(module.RoleFacade):
        if not name.startswith("__") and callable(getattr(module.RoleFacade, name)):
            setattr(probe, name, types.MethodType(getattr(module.RoleFacade, name), probe))
    return probe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offsets", default="0.00,0.10,0.20,0.30,0.40",
                        help="初始横向偏差列表(米)")
    parser.add_argument("--budget", type=float, default=70.0, help="每例时间上限(秒)")
    parser.add_argument("--box", default="", help="绝对摆位: x,y (空旷场地模式)")
    parser.add_argument("--target", default="", help="绝对目标: x,y (空旷场地模式)")
    args = parser.parse_args()

    offsets = [float(v) for v in args.offsets.split(",") if v.strip()]
    if args.box and args.target:
        offsets = [0.0]
    ux = TARGET[0] - BOX[0]
    uy = TARGET[1] - BOX[1]
    ux, uy = PUSH_U
    px, py = -uy, ux                      # 垂直方向(横向)

    print("=== 理论 ===")
    print(f"连线方向 ({ux:+.2f},{uy:+.2f}), 目标横向应为 0")
    for d0 in offsets:
        print(f"  初始横向 {d0:+.2f} m → 应侧挪 {-d0:+.2f} m(一次到位), 之后横向应为 0")
    print()

    module = _load_module()
    results = []
    for d0 in offsets:
        _post("/v1/reset", {})
        time.sleep(0.4)
        # **车不能 place(404), 所以反过来放箱子**: 让"车在默认部署位"恰好等于
        # "箱子后方 + 横向 d0"。推挤方向取 +x: B = R0 + u*STANDOFF - perp*d0
        if args.box and args.target:
            # **空旷场地模式**: 箱子/目标直接给定, 车自己导航过去(车不能 place)
            box_at = tuple(float(v) for v in args.box.split(","))
            target_at = tuple(float(v) for v in args.target.split(","))
        else:
            rover0 = _get(f"{WORLD}/v1/entity/rover")["pos"][:2]
            box_at = (rover0[0] + ux * STANDOFF - px * d0,
                      rover0[1] + uy * STANDOFF - py * d0)
            target_at = (box_at[0] + ux * PUSH_LEN, box_at[1] + uy * PUSH_LEN)
        for _ in range(3):
            # **必须带 z**: 不带 z 会放在 z=0(与地面穿插), 物理瞬间把箱子弹飞
            # 5~7 m —— 实测 place 返回 ok、但箱子出现在 (5.85,0.55)。
            _post("/v1/place", {"body": "obs2", "x": box_at[0],
                                "y": box_at[1], "z": 0.45})
            time.sleep(0.6)
            real = _get(f"{WORLD}/v1/entity/obs2")["pos"]
            if math.hypot(real[0] - box_at[0], real[1] - box_at[1]) <= 0.15:
                break
        BOX_LOCAL = box_at
        TARGET_LOCAL = target_at

        probe = _build_facade(module)
        # **挡住"回合播种"**: 它会按地图部署位覆盖我们的信念(实测因此整轮在追
        # (-0.56,0.24) 那个不存在的箱子, 真箱子被撞出 7 m 远)。
        episode = (_get(f"{WORLD}/v1/health") or {}).get("episode")
        probe._seeded_episode = episode
        probe._episode_fit_confirmed = episode
        probe._set_belief((BOX_LOCAL[0], BOX_LOCAL[1]), var=0.01, source="exp")
        world = module.SharedWorld(WORLD)
        buffer = io.StringIO()
        t0 = time.time()
        with contextlib.redirect_stdout(buffer):
            try:
                ok, detail = probe._push_to_destination_local(
                    world, "obs2", TARGET_LOCAL, "exp")
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"{type(exc).__name__}: {exc}"
        elapsed = time.time() - t0
        log = buffer.getvalue()

        truth = _get(f"{WORLD}/v1/entity/obs2")["pos"]
        dist = math.hypot(truth[0] - TARGET_LOCAL[0], truth[1] - TARGET_LOCAL[1])
        lat_end = abs((truth[0] - TARGET_LOCAL[0]) * px
                      + (truth[1] - TARGET_LOCAL[1]) * py)
        # 把每个 cycle 的动作落盘, 便于查"为什么 10 轮只推进 0.6~1.0 m"
        try:
            out = Path("/var/tmp/rg-run") / f"exp-bias-{d0:.2f}.log"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(log, encoding="utf-8")
        except OSError:
            pass
        fixes = log.count("横向修正")
        bias_used = log.count("应用推偏修正")
        results.append({"d0": d0, "ok": ok, "fixes": fixes, "bias": bias_used,
                        "dist": dist, "lat": lat_end, "secs": elapsed,
                        "box": (round(truth[0], 2), round(truth[1], 2))})
        print(f"d0={d0:+.2f} | 横向修正 {fixes} 次 | 推偏偏置 {bias_used} 次 | "
              f"终点 {results[-1]['box']} | 离目标 {dist:.2f} m | "
              f"离线 {lat_end:.2f} m | {elapsed:.0f} s | {detail[:40]}")

    print()
    print("=== 汇总 ===")
    for row in results:
        print(f"  初始横向 {row['d0']:+.2f} → 修正 {row['fixes']} 次, "
              f"离目标 {row['dist']:.2f} m, 最终离线 {row['lat']:.2f} m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
