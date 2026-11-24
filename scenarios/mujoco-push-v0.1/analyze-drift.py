#!/usr/bin/env python3
"""对齐"信念序列(日志)"与"真值序列(采样)", 找出信念是在哪一步开始偏的。

判定规则(用户的问题: 会不会是"推了但没统计位移"):
    相邻两轮之间:
      d_truth  = 真值位移
      d_belief = 信念位移
      d_truth - d_belief ≥ 0.15 m  → **推了但没统计**(记账丢失)
      d_belief - d_truth ≥ 0.15 m  → 凭空跳变(测量错/锚点污染)
"""

from __future__ import annotations

import argparse
import bisect
import re
from pathlib import Path

CYCLE = re.compile(
    r"^\[(\d\d:\d\d:\d\d)\].*局部推-看 第 (\d+) 轮: 箱子 \(([-\d.]+),([-\d.]+)\)")


def _load_belief(log_path: Path):
    rows = []
    for line in log_path.read_text(encoding="utf-8", errors="ignore").split("\n"):
        hit = CYCLE.search(line)
        if hit:
            rows.append((hit.group(1), int(hit.group(2)),
                         float(hit.group(3)), float(hit.group(4))))
    return rows


def _load_truth(csv_path: Path):
    stamps, pts = [], []
    for line in csv_path.read_text(encoding="utf-8").split("\n")[1:]:
        parts = line.strip().split(",")
        if len(parts) != 3:
            continue
        stamps.append(parts[0])
        pts.append((float(parts[1]), float(parts[2])))
    return stamps, pts


def _truth_at(stamps, pts, stamp: str):
    index = bisect.bisect_left(stamps, stamp)
    index = min(max(index, 0), len(pts) - 1) if pts else None
    return pts[index] if index is not None else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--tol", type=float, default=0.15)
    args = parser.parse_args()

    belief = _load_belief(Path(args.log))
    stamps, pts = _load_truth(Path(args.trace))
    if not belief or not pts:
        print("数据不足: 信念行 %d, 真值行 %d" % (len(belief), len(pts)))
        return 1

    print(f"信念行 {len(belief)} | 真值采样 {len(pts)}")
    print("轮次 |      信念        |      真值        | 真值位移 | 信念位移 | 判定")
    prev = None
    lost = jumped = 0
    for stamp, cycle, bx, by in belief:
        truth = _truth_at(stamps, pts, stamp)
        if truth is None:
            continue
        if prev is None:
            print(f"{cycle:4d} | ({bx:6.2f},{by:6.2f}) | ({truth[0]:6.2f},{truth[1]:6.2f}) "
                  f"|    -     |    -     | 起点(偏差 "
                  f"{((bx - truth[0]) ** 2 + (by - truth[1]) ** 2) ** 0.5:.2f} m)")
        else:
            d_truth = ((truth[0] - prev[1][0]) ** 2 + (truth[1] - prev[1][1]) ** 2) ** 0.5
            d_belief = ((bx - prev[0][0]) ** 2 + (by - prev[0][1]) ** 2) ** 0.5
            verdict = ""
            if d_truth - d_belief >= args.tol:
                verdict = "**推了但没统计**"
                lost += 1
            elif d_belief - d_truth >= args.tol:
                verdict = "**凭空跳变**"
                jumped += 1
            print(f"{cycle:4d} | ({bx:6.2f},{by:6.2f}) | ({truth[0]:6.2f},{truth[1]:6.2f}) "
                  f"|  {d_truth:5.2f}  |  {d_belief:5.2f}  | {verdict}")
        prev = ((bx, by), truth)

    if prev is not None:
        bx, by = prev[0]
        tx, ty = prev[1]
        print(f"\n结束: 信念 ({bx:.2f},{by:.2f}) 真值 ({tx:.2f},{ty:.2f}) "
              f"→ 偏差 {((bx - tx) ** 2 + (by - ty) ** 2) ** 0.5:.2f} m")
    print(f"汇总: 推了但没统计 {lost} 次 | 凭空跳变 {jumped} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
