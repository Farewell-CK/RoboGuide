#!/usr/bin/env python3
"""受控实验: **只测信念定位**(不推、不导航), 一测不到就立刻停。

为什么单列这个实验:
    之前所有失败里, 最常见的一条是"推后测不到 → 信念不更新"(甚至偏 1~1.6 m)。
    那条链上混着 站位/推挤/避障, 分不清是"看不准"还是"推不好"。
    这里把推挤完全去掉: 把箱子放到已知位置 → 给一个**故意偏移的先验** →
    只调用一次测量(双视角拟合) → 与真值比较。
    任何一次测不到(返回 None) → **立即停止**, 打印当时的全部诊断。

用法:
    python scenarios/exp-belief.py                  # 默认 4 个摆放
    python scenarios/exp-belief.py --offset 0.8     # 先验故意偏 0.8 m
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

# 车部署位在 (-0.90,1.30); 这些点都在它周围 0.8~1.2 m, 保证"看得见但先验是错的"
PLACEMENTS = [
    (-0.30, 1.00),
    (-1.45, 1.15),
    (-0.85, 0.45),
    (-0.55, 1.75),
]
BOX_Z = 0.45          # 必须带 z: 不带会放在 z=0, 与地面穿插后被弹飞 5~7 m


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


def _place_box(x: float, y: float) -> tuple[float, float]:
    for _ in range(3):
        _post("/v1/place", {"body": "obs2", "x": x, "y": y, "z": BOX_Z})
        time.sleep(0.6)
        real = _get(f"{WORLD}/v1/entity/obs2")["pos"]
        if math.hypot(real[0] - x, real[1] - y) <= 0.15:
            return (real[0], real[1])
    return (real[0], real[1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offset", type=float, default=0.60,
                        help="先验故意偏移(米), 用来验证'测量是否真的在找箱子'")
    parser.add_argument("--n", type=int, default=len(PLACEMENTS))
    parser.add_argument("--only", type=int, default=0,
                        help="只跑第 i 个摆放(1 起), 0=全部")
    args = parser.parse_args()

    module = _load_module()
    rows = []
    targets = ([(args.only, PLACEMENTS[args.only - 1])] if args.only
               else list(enumerate(PLACEMENTS[:args.n], start=1)))
    for index, (px, py) in targets:
        _post("/v1/reset", {})
        time.sleep(0.5)
        truth = _place_box(px, py)
        prior = (truth[0] + args.offset, truth[1] + args.offset * 0.5)

        probe = _build_facade(module)
        episode = (_get(f"{WORLD}/v1/health") or {}).get("episode")
        probe._seeded_episode = episode          # 挡住"回合播种"覆盖我们的先验
        probe._episode_fit_confirmed = episode
        probe._set_belief(prior, var=0.01, source="exp")
        world = module.SharedWorld(WORLD)

        buffer = io.StringIO()
        t0 = time.time()
        with contextlib.redirect_stdout(buffer):
            try:
                measured = probe._measure_twoview(world, "obs2", prior)
            except Exception as exc:  # noqa: BLE001
                measured = None
                buffer.write(f"EXC {type(exc).__name__}: {exc}\n")
        elapsed = time.time() - t0
        log = buffer.getvalue()
        out = Path("/var/tmp/rg-run") / f"exp-belief-{index}.log"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(log, encoding="utf-8")

        if measured is None:
            print(f"[{index}] 真值 ({truth[0]:.2f},{truth[1]:.2f}) 先验 "
                  f"({prior[0]:.2f},{prior[1]:.2f}) → **测不到**, 立即停止")
            print("  诊断(最后 6 行):")
            for line in log.strip().split("\n")[-6:]:
                print("   ", line[:110])
            print(f"  完整日志: {out}")
            break

        err = math.hypot(measured[0] - truth[0], measured[1] - truth[1])
        prior_err = math.hypot(prior[0] - truth[0], prior[1] - truth[1])
        rows.append((index, truth, prior_err, measured, err, elapsed))
        print(f"[{index}] 真值 ({truth[0]:.2f},{truth[1]:.2f}) | 先验偏差 "
              f"{prior_err:.2f} m → 测得 ({measured[0]:.2f},{measured[1]:.2f}), "
              f"误差 {err:.2f} m | {elapsed:.1f} s")

    print()
    if rows:
        errs = [r[4] for r in rows]
        print(f"=== 汇总: {len(rows)} 例全部测到, 误差 中位 {sorted(errs)[len(errs)//2]:.2f} m, "
              f"最大 {max(errs):.2f} m ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
