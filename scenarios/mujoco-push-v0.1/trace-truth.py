#!/usr/bin/env python3
"""按时间采样"箱子真值", 用于和 facade 日志里的信念序列对齐。

为什么要它:
    我们要回答"信念为什么偏了 1 m"。只看回合结束时的真值不够 —— 需要知道
    **每一次推挤之后箱子实际动了多少**, 再和日志里"信念更新了多少"对比:
      真值动了 ≥0.15 m 而信念没动 → "推了但没统计"(记账丢失)
      真值没动 而信念动了 0.5 m  → "凭空跳变"(测量错/锚点污染)

用法:
    python scenarios/trace-truth.py --seconds 400 --out /var/tmp/rg-run/trace.csv
输出: t,truth_x,truth_y (每 2 s 一行)
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

WORLD = "http://127.0.0.1:28100"


def _pos() -> tuple[float, float] | None:
    try:
        with urllib.request.urlopen(f"{WORLD}/v1/entity/obs2", timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
        return (float(data["pos"][0]), float(data["pos"][1]))
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=400.0)
    parser.add_argument("--period", type=float, default=2.0)
    parser.add_argument("--out", default="/var/tmp/rg-run/trace.csv")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + args.seconds
    with out.open("w", encoding="utf-8") as handle:
        handle.write("t,truth_x,truth_y\n")
        while time.time() < deadline:
            spot = _pos()
            stamp = time.strftime("%H:%M:%S")
            if spot is not None:
                handle.write(f"{stamp},{spot[0]:.3f},{spot[1]:.3f}\n")
                handle.flush()
            time.sleep(args.period)
    print(f"采样完成 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
