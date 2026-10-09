"""本体自带的三维感知、建图与定位 (深度相机 + 激光雷达 -> 体素地图 -> 扫描匹配)。

为什么单独成模块
----------------
`world.py` 里的感知原本是**几何真值判定** (`detect` 直接读 `data.xpos` 判量程/视场/
遮挡), 本体"天生知道"自己在哪、目标在哪。本模块把它换成**本体自己带传感器**的版本:

    真值只用来**产生观测** (射线求交发生在真实世界里, 这是仿真的固有权利);
    本体拿到的只有传感器量程内的距离样本, 位姿一律由自己估计。

因此链路是闭环的, 且每一环都能被质疑:

    编码器 (关节速度, 带尺度/噪声误差)  -> 里程计积分 (会漂移)
    雷达 180 束 x 3 层 + 深度相机 32x24 -> 距离样本 (带距离相关噪声)
    体素占据地图 (log-odds, 0.08 m)     -> 三维地图 (射线 marching 更新)
    扫描匹配 (自建地图的距离场)          -> 位姿校正 (观测纠正漂移)
    导航规划 (自建栅格 A*, 失败才回退)    -> 只用自己的地图

坐标系
------
地图系在开机时由一次"开机定位"与世界系对齐 (真值 + 0.10 m / 3° 误差), 之后不再读真
值; 位姿估计 `(x, y, yaw)` 因此始终落在世界系里, Mission 的语义目的地可以直接使用。
本体系: 前 = +x, 左 = +y, 上 = +z, 与 MJCF 里 `rover_cam` / `dog_cam` 的朝向一致。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

import mujoco

try:  # 距离场用 EDT, 缺 scipy 时退化成暴力最近邻
    from scipy import ndimage
except Exception:  # pragma: no cover - scipy 是本模块的可选加速依赖
    ndimage = None


# ------------------------------ 常量 ------------------------------

VOXEL_M = 0.06                  # 体素边长 (三维地图分辨率; 它同时决定匹配精度下限)
MAP_XLIM = (-4.6, 4.6)          # 地图范围 (覆盖整个竞技场)
MAP_YLIM = (-4.6, 4.6)
MAP_ZLIM = (-0.10, 2.40)
LOG_MIN, LOG_MAX = -4.0, 6.0    # log-odds 截断
OCC_LOG = 0.55                  # 命中: 占据增量
FREE_LOG = 0.12                 # 穿过: 空闲增量
OCC_THR = 0.80                  # 判定为占据的 log-odds 阈值 (高于一次命中, 抑制孤立噪点)

# 建图/投影用的高度带: 低于此高度的是地面反射 (水平雷达必然会打到地板), 高于此
# 高度的是天花板/无关结构, 都不应进二维投影栅格。
MAP_Z_LO, MAP_Z_HI = 0.05, 1.60

# 开机定位误差 (本体并非"天生知道自己在哪", 但地图系要与世界系对齐才能用语义目的地)
BOOT_POS_SIGMA_M = 0.10
BOOT_YAW_SIGMA_RAD = math.radians(3.0)
# 编码器误差: 尺度因子 + 白噪声 (足式/轮式里程计的打滑与量化)
ODO_SCALE_SIGMA = 0.06
ODO_NOISE_MPS = 0.05
ODO_YAW_NOISE_RPS = 0.030
# 在线标定: 累积到这么长的位移窗口才更新一次尺度; 增益是低通系数
# 关键帧 ICP: 相对关键帧走出这么远 (或转过这么多) 才做一次对齐
KEYFRAME_M = 0.25
KEYFRAME_RAD = 0.15
CALIB_WINDOW_M = 0.40
CALIB_GAIN = 0.50
# 地图匹配的介入阈值: 分数高于它说明"自洽", 不动位姿
MAP_ALERT_SCORE = 0.90
# 识别层 (ArUco 解码 / 物体检测) 输出的误差模型: 它给的是目标相对本体的**方位与
# 距离**, 而不是世界坐标。误差随距离增长 (远了成像小、角分辨率不足)。
# 注意识别层**可能什么都不给** (不在视场/遮挡/太远), 那时本体就是不知道目标在哪,
# 而不是仍能拿到精确坐标 —— 这是与"直接读目标真值"的根本区别。
PERCEPT_DIR_SIGMA_RAD = 0.012          # 方位误差 (~0.7°)
PERCEPT_RANGE_SIGMA_M = 0.020          # 测距误差的常数项
PERCEPT_RANGE_SIGMA_K = 0.012          # 测距误差随距离增长的分量 (2 m 处约 4.4 cm)
# 回波距离要与识别报的距离吻合到这个程度才算"看到的确实是它"而不是别的东西
PERCEPT_ECHO_TOL_M = 0.18


def _wrap(angle: float) -> float:
    """把角度归一化到 (-pi, pi]。"""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


# ------------------------------ 传感器规格 ------------------------------


@dataclass(frozen=True)
class DepthCamSpec:
    """本体前方的深度相机 (安装位给出的是本体系坐标)。"""

    pos: tuple[float, float, float] = (0.20, 0.0, 0.05)
    fov_v_deg: float = 70.0        # MJCF: rover_cam / dog_cam fovy
    width: int = 32                # 深度图分辨率 (射线数 = W*H)
    height: int = 24
    min_m: float = 0.15
    max_m: float = 4.00
    hz: float = 5.0
    sigma_k: float = 0.0035        # 深度噪声 ~ k * d^2 (Kinect 型模型)

    def rays(self) -> np.ndarray:
        """相机本体系下的采样方向 (H*W, 3)。"""
        w, h = self.width, self.height
        fov_v = math.radians(self.fov_v_deg)
        fov_h = 2.0 * math.atan(math.tan(fov_v / 2.0) * w / h)
        us = (np.arange(w) + 0.5) / w - 0.5      # (-0.5, 0.5)
        vs = (np.arange(h) + 0.5) / h - 0.5
        yaw = us * fov_h                          # (-fov_h/2, +fov_h/2)
        pit = -vs * fov_v                         # 行序自上而下
        yy, pp = np.meshgrid(yaw, pit, indexing="xy")
        cp = np.cos(pp)
        return np.stack([cp * np.cos(yy), cp * np.sin(yy), np.sin(pp)], -1).reshape(-1, 3)


@dataclass(frozen=True)
class LidarSpec:
    """本体顶部 360° 激光雷达 (多层俯仰 => 三维点云)。"""

    pos: tuple[float, float, float] = (0.0, 0.0, 0.12)
    beams: int = 180                       # 水平角分辨率 2°
    pitches_deg: tuple[float, ...] = (-6.0, 0.0, 6.0)
    min_m: float = 0.12
    max_m: float = 8.00
    hz: float = 10.0
    sigma_m: float = 0.010                 # 测距噪声 (常数项, 与距离弱相关)

    def rays(self) -> np.ndarray:
        """雷达本体系下的采样方向 (beams*len(pitches), 3)。"""
        az = np.arange(self.beams) * (2.0 * math.pi / self.beams)
        blocks = []
        for p in self.pitches_deg:
            pr = math.radians(p)
            blocks.append(np.stack([np.cos(pr) * np.cos(az),
                                    np.cos(pr) * np.sin(az),
                                    np.full(self.beams, math.sin(pr))], -1))
        return np.concatenate(blocks, axis=0)

    def horizontal_mask(self) -> np.ndarray:
        """挑出俯仰 0 的那一层 (扫描匹配只用平面点, 省一半计算)。"""
        n = self.beams
        return np.arange(len(self.pitches_deg) * n) // n == list(self.pitches_deg).index(0.0)


@dataclass(frozen=True)
class SensorRig:
    """一套传感器: 雷达 + 深度相机; 装在哪个 body 上由 RoleSpec 决定。"""

    lidar: LidarSpec = field(default_factory=LidarSpec)
    cam: DepthCamSpec = field(default_factory=DepthCamSpec)
    # 识别 (ArUco / 物体) 只能靠相机, 因此"看见"的判定用相机视场而不是雷达全向
    cam_fov_half_deg: float = 35.0


# ------------------------------ 射线投射 ------------------------------


class RayCaster:
    """对 MuJoCo 世界做射线求交 (CPU, 不依赖 GL, 因此可在无显示环境跑)。"""

    def __init__(self, world: Any, body: str) -> None:
        self.world = world
        self._bid = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_BODY, body)
        self._gid = np.zeros(1, np.int32)

    def cast(self, origin: np.ndarray, dirs: np.ndarray) -> np.ndarray:
        """从 origin 沿 dirs 求最近命中距离; 未命中返回 -1。"""
        m, d = self.world.model, self.world.data
        out = np.empty(len(dirs), dtype=float)
        o = np.ascontiguousarray(origin, dtype=np.float64)
        for i in range(len(dirs)):
            v = np.ascontiguousarray(dirs[i], dtype=np.float64)
            out[i] = mujoco.mj_ray(m, d, o, v, None, 1, self._bid, self._gid)
        return out


# ------------------------------ 三维体素地图 ------------------------------


class VoxelMap:
    """log-odds 体素占据地图。

    更新沿射线进行: 命中点累加占据, 命中之前的沿途体素累加空闲 (未命中的射线按
    量程走完, 同样标空闲)。这是标准 occupancy mapping, 因此"墙后面是未知"而不是
    "墙后面是空" —— 未知区域在规划里按**乐观可通过**处理, 与真实机器人一致。
    """

    def __init__(self, voxel: float = VOXEL_M,
                 xlim: tuple[float, float] = MAP_XLIM,
                 ylim: tuple[float, float] = MAP_YLIM,
                 zlim: tuple[float, float] = MAP_ZLIM) -> None:
        self.voxel = float(voxel)
        self.xlim, self.ylim, self.zlim = xlim, ylim, zlim
        self.nx = int(round((xlim[1] - xlim[0]) / self.voxel))
        self.ny = int(round((ylim[1] - ylim[0]) / self.voxel))
        self.nz = int(round((zlim[1] - zlim[0]) / self.voxel))
        self.log = np.zeros((self.nx, self.ny, self.nz), dtype=np.float32)
        # seen: 至少被一条射线扫过 (free 或 occ) —— 区分"未知"与"空闲"
        self.seen = np.zeros((self.nx, self.ny), dtype=bool)
        self._field: np.ndarray | None = None       # 二维距离场缓存
        self.stats = {"rays": 0, "hits": 0, "updates": 0}

    # ---------------- 索引 ----------------

    def idx_of(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """世界点 -> 体素下标 (N,3) 与有效掩码。"""
        p = np.atleast_2d(np.asarray(pts, dtype=float))
        i = np.floor((p[:, 0] - self.xlim[0]) / self.voxel).astype(np.int32)
        j = np.floor((p[:, 1] - self.ylim[0]) / self.voxel).astype(np.int32)
        k = np.floor((p[:, 2] - self.zlim[0]) / self.voxel).astype(np.int32)
        ok = ((i >= 0) & (i < self.nx) & (j >= 0) & (j < self.ny)
              & (k >= 0) & (k < self.nz))
        return np.stack([i, j, k], axis=1), ok

    def center_of(self, idx: np.ndarray) -> np.ndarray:
        """体素下标 -> 世界中心坐标。"""
        idx = np.atleast_2d(np.asarray(idx, dtype=float))
        return np.stack([
            self.xlim[0] + (idx[:, 0] + 0.5) * self.voxel,
            self.ylim[0] + (idx[:, 1] + 0.5) * self.voxel,
            self.zlim[0] + (idx[:, 2] + 0.5) * self.voxel,
        ], axis=1)

    # ---------------- 更新 ----------------

    def integrate(self, origin: np.ndarray, dirs: np.ndarray, dists: np.ndarray,
                  max_range: float, z_lo: float = MAP_Z_LO, z_hi: float = 2.2,
                  free_samples: int = 10) -> int:
        """把一次扫描融合进地图; 返回本次标记为占据的体素数。"""
        d = np.asarray(dists, dtype=float)
        hit = (d > 0.0) & (d <= max_range)
        self.stats["rays"] += int(len(d))
        self.stats["hits"] += int(hit.sum())
        if not hit.any():
            return 0
        dirs_h = np.asarray(dirs, dtype=float)[hit]
        d_h = d[hit]

        # --- 占据: 命中点 (落在高度带内的才记入三维地图) ---
        pts = origin[None, :] + dirs_h * d_h[:, None]
        idx, ok = self.idx_of(pts)
        zin = (pts[:, 2] >= z_lo) & (pts[:, 2] <= z_hi)
        ok &= zin
        if ok.any():
            good = idx[ok]
            np.add.at(self.log, (good[:, 0], good[:, 1], good[:, 2]), OCC_LOG)
        n_occ = int(ok.sum())

        # --- 空闲: 命中之前的沿途体素 (按比例采样, 与距离无关的固定开销) ---
        ts = np.linspace(0.12, 0.94, free_samples)
        along = origin[None, :] + dirs_h[:, None, :] * (d_h[:, None] * ts[None, :])[:, :, None]
        flat = along.reshape(-1, 3)
        fidx, fok = self.idx_of(flat)
        fok &= (flat[:, 2] >= z_lo) & (flat[:, 2] <= z_hi)
        if fok.any():
            good = fidx[fok]
            np.add.at(self.log, (good[:, 0], good[:, 1], good[:, 2]), -FREE_LOG)
            self.seen[good[:, 0], good[:, 1]] = True

        np.clip(self.log, LOG_MIN, LOG_MAX, out=self.log)
        self._field = None
        self.stats["updates"] += 1
        return n_occ

    # ---------------- 查询 ----------------

    def occupied(self) -> np.ndarray:
        """占据体素的下标 (N,3)。"""
        return np.argwhere(self.log > OCC_THR)

    def occupied_centers(self, z_lo: float = MAP_Z_LO,
                         z_hi: float = MAP_Z_HI) -> np.ndarray:
        """占据体素的世界中心 (按高度带过滤, 用于点云输出/统计)。"""
        idx = self.occupied()
        if len(idx) == 0:
            return np.zeros((0, 3))
        c = self.center_of(idx)
        return c[(c[:, 2] >= z_lo) & (c[:, 2] <= z_hi)]

    def clear_disc(self, center_xy: np.ndarray, radius_m: float,
                   z_lo: float = MAP_Z_LO, z_hi: float = MAP_Z_HI) -> int:
        """把一块区域标为**已空**: 那个东西被推走了, 那里不再是障碍。

        地图是累积的, 动态物体离开后它的旧位置仍是占据 —— 而本体随后正好站在
        那个位置上, 于是规划时判"起点在障碍里", 撤离动作因此失败过。依据是本体
        自己的观测 (测得它移动了), 不是真值; 这也是真实动态环境 SLAM 必须做的
        地图维护, 只是这里由推挤这一已知交互显式触发。
        """
        c = np.asarray(center_xy, dtype=float)
        xs = self.xlim[0] + (np.arange(self.nx) + 0.5) * self.voxel
        ys = self.ylim[0] + (np.arange(self.ny) + 0.5) * self.voxel
        zs = self.zlim[0] + (np.arange(self.nz) + 0.5) * self.voxel
        disc = ((xs[:, None] - c[0]) ** 2
                + (ys[None, :] - c[1]) ** 2) <= radius_m ** 2
        band = (zs >= z_lo) & (zs <= z_hi)
        sel = disc[:, :, None] & band[None, None, :]
        self.log[sel] = np.minimum(self.log[sel], -OCC_THR)
        self.seen[disc] = True
        self._field = None
        return int(disc.sum())

    def grid2d(self, z_lo: float = MAP_Z_LO, z_hi: float = MAP_Z_HI) -> np.ndarray:
        """投影成二维占据栅格 (nx, ny): True = 该竖列内有实体。"""
        idx = self.occupied()
        g = np.zeros((self.nx, self.ny), dtype=bool)
        if len(idx) == 0:
            return g
        c = self.center_of(idx)
        m = (c[:, 2] >= z_lo) & (c[:, 2] <= z_hi)
        if m.any():
            g[idx[m, 0], idx[m, 1]] = True
        return g

    def distance_field(self, z_lo: float = MAP_Z_LO,
                       z_hi: float = MAP_Z_HI) -> np.ndarray:
        """每格到最近占据格的距离 (米); 扫描匹配用它做 O(1) 查表打分。"""
        if self._field is None:
            occ = self.grid2d(z_lo, z_hi)
            if ndimage is not None:
                edt = ndimage.distance_transform_edt(~occ)
            else:  # pragma: no cover - 无 scipy 时的退化路径
                ys, xs = np.nonzero(occ)
                if len(xs) == 0:
                    edt = np.full(occ.shape, 1e3, dtype=float)
                else:
                    ii, jj = np.mgrid[0:self.nx, 0:self.ny]
                    edt = np.min(
                        (ii[:, :, None] - xs[None, None, :]) ** 2
                        + (jj[:, :, None] - ys[None, None, :]) ** 2, axis=2) ** 0.5
            self._field = (edt * self.voxel).astype(np.float32)
        return self._field

    def coverage(self) -> float:
        """已探索的二维格占比。"""
        return float(self.seen.mean()) if self.seen.size else 0.0

    # ---------------- 导出 ----------------

    def as_dict(self) -> dict[str, Any]:
        """给轨迹/报告用的摘要。"""
        occ = self.grid2d()
        return {
            "voxel_m": self.voxel,
            "occupied_voxels": int((self.log > OCC_THR).sum()),
            "occupied_cells_2d": int(occ.sum()),
            "coverage": round(self.coverage(), 4),
            "rays_cast": self.stats["rays"],
            "rays_hit": self.stats["hits"],
            "scans_fused": self.stats["updates"],
        }


# ------------------------------ 扫描匹配定位 ------------------------------


class ScanMatcher:
    """在自建地图里做扫描匹配: 用距离场做粗到细的 (x, y, yaw) 搜索。

    为什么是距离场而不是 KD-tree: 地图每帧都在变, 而 EDT 一次只需 ~1 ms, 之后每次
    打分退化为查表; 三级 5x5x5 网格共 375 次打分约 10 ms, 在 5 Hz 下占不到 5% CPU。
    """

    def __init__(self, sigma_m: float = 0.12, stages: Iterable[tuple[float, float]] =
                 ((0.30, 0.12), (0.10, 0.04), (0.03, 0.010)),
                 min_gain: float = 0.004) -> None:
        self.sigma = float(sigma_m)
        self.stages = tuple(stages)
        self.min_gain = float(min_gain)

    def score(self, pts: np.ndarray, x: float, y: float, yaw: float,
              field: np.ndarray, map_: VoxelMap) -> float:
        """把本体系扫描点按候选位姿投到地图里, 命中地图实体的比例即分数。"""
        c, s = math.cos(yaw), math.sin(yaw)
        wx = x + pts[:, 0] * c - pts[:, 1] * s
        wy = y + pts[:, 0] * s + pts[:, 1] * c
        i = np.rint((wx - map_.xlim[0]) / map_.voxel).astype(np.int32)
        j = np.rint((wy - map_.ylim[0]) / map_.voxel).astype(np.int32)
        ok = (i >= 0) & (i < map_.nx) & (j >= 0) & (j < map_.ny)
        if not ok.all():
            # 投到地图外的点按"很差"计, 避免位姿靠"把点扔出地图"刷高分
            d = np.full(len(pts), self.sigma * 4.0)
            d[ok] = field[i[ok], j[ok]]
        else:
            d = field[i, j]
        return float(np.mean(np.exp(-(d / self.sigma) ** 2)))

    def align(self, pts: np.ndarray, prior: np.ndarray, field: np.ndarray,
              map_: VoxelMap) -> tuple[np.ndarray, float]:
        """在先验位姿附近搜索最优 (x, y, yaw); 返回 (位姿, 分数)。"""
        best = np.asarray(prior, dtype=float).copy()
        best_s = self.score(pts, best[0], best[1], best[2], field, map_)
        for span_xy, span_yaw in self.stages:
            cand: list[np.ndarray] = []
            for dx in np.linspace(-span_xy, span_xy, 5):
                for dy in np.linspace(-span_xy, span_xy, 5):
                    for da in np.linspace(-span_yaw, span_yaw, 5):
                        cand.append(best + np.array([dx, dy, da]))
            scores = [self.score(pts, c[0], c[1], c[2], field, map_) for c in cand]
            k = int(np.argmax(scores))
            # 只有显著改善才算"地图给出了新信息": 分数饱和 (扫描与地图已经贴合)
            # 时, 网格里会有一片同样满分的 plateau, 采纳它等于注入一次随机游走。
            if scores[k] > best_s + self.min_gain:
                best_s = scores[k]
                best = cand[k]
        return best, best_s


# ------------------------------ 扫描间 ICP (相对运动) ------------------------------


class Icp2D:
    """把当前扫描对齐到上一帧扫描: 点-点 ICP 求 (dx, dy, dyaw)。

    为什么还需要它 (有了地图匹配还不够): 地图是**用估计位姿自己建的**, 位姿偏了
    地图就整体跟着偏, 于是"扫描 vs 地图"永远自洽 —— 匹配只看得到自己, 绝对误差
    既发现不了也纠正不了 (实测漂移 0.5 m/20 s, 而匹配分数始终 0.98)。
    扫描间 ICP 的参考是**上一帧的真实观测**, 与地图无关, 因此给出的是高精度
    **相对运动**; 绝对漂移因此退化成 ICP 残差的随机游走 (厘米级), 而不是地图误差。
    """

    def __init__(self, max_corr_m: float = 0.40, iters: int = 6,
                 min_corr: int = 20) -> None:
        # 对应阈值逐轮收紧: 第一轮宽松收敛, 后面剔除残差大的对应。
        # 不收紧的话, 被推走的障碍 (obs2) 会被当成"本体自己在动" —— 实测它单独
        # 就能把位姿带偏 0.4 m。
        self.max_corr = float(max_corr_m)
        self.iters = int(iters)
        self.min_corr = int(min_corr)
        try:
            from scipy.spatial import cKDTree
        except Exception:  # pragma: no cover
            cKDTree = None
        self._kdtree = cKDTree

    def _to_world(self, pts: np.ndarray, pose: np.ndarray) -> np.ndarray:
        c, s = math.cos(pose[2]), math.sin(pose[2])
        return np.stack([pose[0] + pts[:, 0] * c - pts[:, 1] * s,
                         pose[1] + pts[:, 0] * s + pts[:, 1] * c], axis=1)

    def align(self, pts_local: np.ndarray, ref_world: np.ndarray,
              prior: np.ndarray) -> tuple[np.ndarray, float]:
        """在 prior 上叠加 ICP 修正; 返回 (新位姿, 平均残差)。"""
        if self._kdtree is None or len(ref_world) < self.min_corr:
            return np.asarray(prior, dtype=float).copy(), float("nan")
        tree = self._kdtree(ref_world)
        pose = np.asarray(prior, dtype=float).copy()
        resid = float("nan")
        for it in range(self.iters):
            w = self._to_world(pts_local, pose)
            d, idx = tree.query(w)
            keep = d < max(0.08, self.max_corr * (0.65 ** it))
            if int(keep.sum()) < self.min_corr:
                break
            q = ref_world[idx[keep]]
            p = pts_local[keep]
            c, s = math.cos(pose[2]), math.sin(pose[2])
            # 残差 e = q - (t + R p); 雅可比: dt -> I, dyaw -> dR/dyaw * p
            rx = pose[0] + p[:, 0] * c - p[:, 1] * s
            ry = pose[1] + p[:, 0] * s + p[:, 1] * c
            ex = q[:, 0] - rx
            ey = q[:, 1] - ry
            jx = -p[:, 0] * s - p[:, 1] * c       # d rx / d yaw
            jy = p[:, 0] * c - p[:, 1] * s        # d ry / d yaw
            A = np.zeros((2, 3))
            b = np.zeros(2)
            # 正规方程 (A^T A) delta = A^T e, 对每个点累加
            J = np.stack([np.ones_like(p[:, 0]), np.zeros_like(p[:, 0]), jx,
                          np.zeros_like(p[:, 0]), np.ones_like(p[:, 0]), jy], axis=1)
            J = J.reshape(-1, 2, 3)
            e = np.stack([ex, ey], axis=1).reshape(-1, 2, 1)
            H = np.einsum("nij,nik->jk", J, J)
            g = np.einsum("nij,nil->jl", J, e).reshape(3)
            try:
                delta = np.linalg.solve(H + np.eye(3) * 1e-6, g)
            except np.linalg.LinAlgError:
                break
            pose = pose + delta
            pose[2] = _wrap(pose[2])
            resid = float(np.hypot(ex, ey).mean())
            if np.linalg.norm(delta) < 1e-4:
                break
        return pose, resid


# ------------------------------ 自建栅格上的 A* ------------------------------


class PlanGrid:
    """在**自建**二维投影栅格上做 A* (未知区域按可通过处理)。

    膨胀格数必须覆盖本体自己的外接半径: 车 0.22 m / 体素 0.06 m ≈ 4 格。早期取 2 格
    (0.12 m) 时, 规划出的对角线会贴着障碍走, 车头顶上去就卡死 (实测单独派车直冲
    顶出点时会原地耗到超时)。
    """

    def __init__(self, map_: VoxelMap, inflate_cells: int = 4) -> None:
        self.map = map_
        self.inflate = int(inflate_cells)
        self.fail_reason = ""          # 上一次 plan 失败的原因 (成功时为空)

    def _grid(self) -> np.ndarray:
        occ = self.map.grid2d()
        if self.inflate <= 0:
            return occ
        if ndimage is not None:
            return ndimage.binary_dilation(occ,
                                           iterations=self.inflate)
        out = occ.copy()
        for _ in range(self.inflate):
            out[1:, :] |= out[:-1, :].copy()
            out[:-1, :] |= out[1:, :].copy()
            out[:, 1:] |= out[:, :-1].copy()
            out[:, :-1] |= out[:, 1:].copy()
        return out

    def disc_mask(self, center: np.ndarray, radius: float) -> np.ndarray:
        """以 center 为心、radius 为半径的圆盘覆盖到的格子 (nx, ny)。"""
        m = self.map
        xs = m.xlim[0] + (np.arange(m.nx) + 0.5) * m.voxel
        ys = m.ylim[0] + (np.arange(m.ny) + 0.5) * m.voxel
        return ((xs[:, None] - center[0]) ** 2
                + (ys[None, :] - center[1]) ** 2) <= radius * radius

    def plan(self, start: np.ndarray, goal: np.ndarray,
             pushable: tuple[np.ndarray, float] | None = None
             ) -> list[np.ndarray] | None:
        """在世界坐标间规划; 失败返回 None (调用方决定是否回退)。

        `pushable=(中心, 半径)` 标出一块**允许穿越**的区域: 上层的推挤任务会告诉
        本体"这个东西是可以被顶开的", 于是它在规划时不再是障碍, 路径可以压过去
        —— 接触式操作 (把箱子顶走) 的前提就是路径**不能**绕开它。本体自己建出来
        的地图只认几何占据, 分不出"墙"和"可推动的箱子", 所以这一层语义只能由任务
        先验给出 (真实系统里同样如此: 人下达"把这箱子推开"时已隐含它可推)。
        注意该区域只对**本体认为可推的目标**生效, 不是全局豁免。

        失败原因记到 `self.fail_reason`: 自己的地图规划不出来时, "起点在障碍里"、
        "目标在障碍里" 与 "不可达" 是完全不同的三种问题, 必须能区分, 否则只能看到
        一个笼统的 no-path。

        两级约束 (与真实 costmap 一致):
          raw 占据  = 硬约束。路径不可穿越, 起点/终点也不能落在里面。
          膨胀层    = 软约束 (安全余量)。搜索时避让, 但**允许**起点/终点位于其中 ——
                      本体贴着障碍站着 (顶推动作的必然结果) 是物理合法的, 目标也可
                      能就在障碍旁边 (例如目的地就是 obs2 本身)。拿膨胀去否决"我在
                      哪儿"会把这类任务判死: 实测占据格就在起点旁 1 格 (6 cm), 于是
                      2/3/4 格膨胀全都把起点判成占据, 缩小膨胀根本无解。
        """
        m = self.map
        g = self._grid()
        raw = m.grid2d()
        if pushable is not None:
            soft = self.disc_mask(np.asarray(pushable[0], dtype=float), float(pushable[1]))
            # 可推动体: 既不是硬阻挡 (要压过去), 也不该再收膨胀代价 (否则仍会绕行)
            raw = raw & ~soft
            g = g & ~soft
        si = int(np.floor((start[0] - m.xlim[0]) / m.voxel))
        sj = int(np.floor((start[1] - m.ylim[0]) / m.voxel))
        gi = int(np.floor((goal[0] - m.xlim[0]) / m.voxel))
        gj = int(np.floor((goal[1] - m.ylim[0]) / m.voxel))
        for v, n in ((si, m.nx), (sj, m.ny), (gi, m.nx), (gj, m.ny)):
            if not (0 <= v < n):
                self.fail_reason = "out-of-map"
                return None
        if raw[si, sj]:
            self.fail_reason = "start-occupied"
            return None
        if raw[gi, gj]:
            self.fail_reason = "goal-occupied"
            return None
        # 膨胀层对起点/终点豁免: 站在安全余量里不是错, 走不出去才是。
        g[si, sj] = False
        g[gi, gj] = False
        # Dijkstra/A*: raw 是硬阻挡, 膨胀层是**额外代价**而不是禁止 —— 本体贴着障碍
        # 站着时, 四周可能整个落在安全余量里, 禁止穿越就等于把它关在里面
        # (实测 3/4 格膨胀下直接 unreachable)。给一个足够大的 penalty, 有自由路时
        # 一定绕行, 没路时才从余量里蹭出来。
        import heapq
        inflate_penalty = 6.0
        open_h: list[tuple[float, int, int]] = [(0.0, si, sj)]
        came: dict[tuple[int, int], tuple[int, int]] = {}
        cost: dict[tuple[int, int], float] = {(si, sj): 0.0}
        nbrs = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]
        while open_h:
            _, ci, cj = heapq.heappop(open_h)
            if (ci, cj) == (gi, gj):
                path = [(ci, cj)]
                while path[-1] in came:
                    path.append(came[path[-1]])
                return [m.center_of(np.array([[i, j, 0]]))[0][:2] for i, j in path[::-1]]
            for di, dj, w in nbrs:
                ni, nj = ci + di, cj + dj
                if not (0 <= ni < m.nx and 0 <= nj < m.ny) or raw[ni, nj]:
                    continue
                if di and dj and (raw[ci + di, cj] or raw[ci, cj + dj]):
                    continue
                nc = cost[(ci, cj)] + w + (inflate_penalty if g[ni, nj] else 0.0)
                if nc < cost.get((ni, nj), 1e18):
                    cost[(ni, nj)] = nc
                    came[(ni, nj)] = (ci, cj)
                    heapq.heappush(open_h, (nc + math.hypot(gi - ni, gj - nj), ni, nj))
        self.fail_reason = "unreachable"
        return None


# ------------------------------ 一个本体的 SLAM ------------------------------


class SlamNode:
    """把传感器、地图、定位串起来的一个本体 SLAM。

    调用方 (RoleRuntime.tick) 每个物理步调 `step(dt)`; 传感器按各自 hz 采样,
    `pose` 是**本体自己认为的**位姿, `pose_true` 只用于事后评估误差。
    """

    def __init__(self, world: Any, body: str, yaw_joint: str,
                 slide: tuple[str, str] | None, rig: SensorRig,
                 seed: int = 0, match_hz: float = 2.5,
                 base_z: float = 0.0) -> None:
        self.world = world
        self.body = body
        self.yaw_joint = yaw_joint
        self.slide = slide
        self.rig = rig
        self.rng = np.random.default_rng(seed)
        # 识别层单独一条随机流: 新增的观测通道不该改变既有传感器噪声的消耗序列,
        # 否则同一 seed 下里程计/雷达/摄像机的历史结果不再可复现。
        self.rng_percept = np.random.default_rng(seed + 1013904223)
        # 基座离地高度是本体自己已知的常量 (传感器安装高度), 不是从仿真真值读来的
        self.base_z = float(base_z)
        self.match_hz = float(match_hz)
        self.gain = 0.6        # 匹配修正的阻尼: 里程计已经很准, 匹配只做缓慢纠正
        # 在线标定: scale_factor 是"里程计读数 -> 真实位移"的比例, 出厂时含误差,
        # 由每次地图匹配给出的实测位移与里程计位移之比低通更新。
        self.scale_factor = 1.0 + float(self.rng.normal(0, ODO_SCALE_SIGMA))
        self.scale_est = self.scale_factor
        self.caster = RayCaster(world, body)
        self.map = VoxelMap()
        self.matcher = ScanMatcher()
        self.planner = PlanGrid(self.map)

        self.lidar_local = rig.lidar.rays()
        self.lidar_hor = rig.lidar.horizontal_mask()
        self.cam_local = rig.cam.rays()

        # 开机定位: 世界系真值 + 一次性的位姿误差 (此后不再读真值)
        t = self._true_pose()
        self.pose = np.array([
            t[0] + self.rng.normal(0, BOOT_POS_SIGMA_M),
            t[1] + self.rng.normal(0, BOOT_POS_SIGMA_M),
            _wrap(t[2] + self.rng.normal(0, BOOT_YAW_SIGMA_RAD))], dtype=float)
        # 出厂未标定的尺度误差 (用于事后对照: scale_factor 应收敛到它的倒数附近)
        self.scale_err = float(self.scale_factor - 1.0)
        self._odo_since_calib = np.zeros(3)      # 里程计窗口
        self._icp_since_calib = np.zeros(3)      # 同期由 ICP 观测到的位移窗口
        self.calibrations = 0

        self._t_lidar = -1.0
        self._t_cam = -1.0
        self._t_match = -1.0
        self.last_score = 0.0
        self.last_scan_pts: np.ndarray = np.zeros((0, 2))
        self.matches = 0
        self.samples = 0
        self.plan_source = "self-map"
        # ICP 关键帧: (本体系扫描点, 该帧的估计位姿)
        self.prev_scan_world: np.ndarray | None = None
        self.icp = Icp2D()
        self.icp_gain = 0.5
        self.last_resid = float("nan")
        self.icp_runs = 0
        self._icp_key: tuple[np.ndarray, np.ndarray] | None = None
        # 误差统计 (评估用, 不参与控制)
        self.err_sum = 0.0
        self.err_max = 0.0
        self.err_n = 0

    # ---------------- 位姿 ----------------

    def _true_pose(self) -> np.ndarray:
        """世界真值位姿 —— 只用于开机对齐与事后误差评估。"""
        w = self.world
        bid = mujoco.mj_name2id(w.model, mujoco.mjtObj.mjOBJ_BODY, self.body)
        p = np.array(w.data.xpos[bid], dtype=float)
        yaw = float(w.qpos(self.yaw_joint)) if self.yaw_joint else 0.0
        return np.array([p[0], p[1], yaw])

    def pose_est(self) -> np.ndarray:
        return self.pose.copy()

    def xy_est(self) -> np.ndarray:
        return self.pose[:2].copy()

    def yaw_est(self) -> float:
        return float(self.pose[2])

    def error(self) -> float:
        """当前估计与真值的平面误差 (米)。"""
        t = self._true_pose()
        return float(np.linalg.norm(t[:2] - self.pose[:2]))

    # ---------------- 每步推进 ----------------

    def step(self, dt: float) -> None:
        """推进一个物理步: 里程计预测 -> 采样 -> 匹配校正 -> 地图更新。"""
        now = self.world.sim_time
        self._odometry(dt)
        do_lidar = self._t_lidar < 0 or (now - self._t_lidar) >= 1.0 / self.rig.lidar.hz
        do_cam = self._t_cam < 0 or (now - self._t_cam) >= 1.0 / self.rig.cam.hz
        do_match = self._t_match < 0 or (now - self._t_match) >= 1.0 / self.match_hz

        if do_lidar:
            self._t_lidar = now
            self._sample_lidar()
            self.samples += 1
        if do_cam:
            self._t_cam = now
            self._sample_camera()
        if do_match and len(self.last_scan_pts) >= 12:
            self._t_match = now
            self._match()
        self._track_error()

    def _odometry(self, dt: float) -> None:
        """编码器里程计: 关节速度积分 + 尺度/噪声误差 (会漂移, 由匹配纠正)。"""
        if self.slide is None:
            return
        w = self.world
        try:
            vx = w.data.qvel[w.model.jnt_dofadr[w._jnt[self.slide[0]]]]
            vy = w.data.qvel[w.model.jnt_dofadr[w._jnt[self.slide[1]]]]
            wz = w.data.qvel[w.model.jnt_dofadr[w._jnt[self.yaw_joint]]]
        except KeyError:
            return
        s = self.scale_factor
        dx = (float(vx) * s + self.rng.normal(0, ODO_NOISE_MPS)) * dt
        dy = (float(vy) * s + self.rng.normal(0, ODO_NOISE_MPS)) * dt
        dyaw = (float(wz) * s + self.rng.normal(0, ODO_YAW_NOISE_RPS)) * dt
        self.pose[0] += dx
        self.pose[1] += dy
        self.pose[2] = _wrap(self.pose[2] + dyaw)
        self._odo_since_calib += np.array([dx, dy, dyaw])

    # ---------------- 采样 ----------------

    def _rig_origin(self, local: tuple[float, float, float]) -> tuple[np.ndarray, np.ndarray]:
        """传感器在世界系的原点与本体旋转矩阵。"""
        w = self.world
        bid = mujoco.mj_name2id(w.model, mujoco.mjtObj.mjOBJ_BODY, self.body)
        rot = np.array(w.data.xmat[bid], dtype=float).reshape(3, 3)
        pos = np.array(w.data.xpos[bid], dtype=float)
        return pos + rot @ np.asarray(local, dtype=float), rot

    def _sample_lidar(self) -> None:
        o, rot = self._rig_origin(self.rig.lidar.pos)
        dirs = self.lidar_local @ rot.T
        d = self.caster.cast(o, dirs)
        spec = self.rig.lidar
        noise = self.rng.normal(0, spec.sigma_m, size=d.shape)
        dd = d + noise
        valid = (dd > spec.min_m) & (dd <= spec.max_m)
        hit = np.where(valid, dd, -1.0)
        # 本体系点云 (供扫描匹配用; 只取水平层, 省一半计算)
        pts_local = self.lidar_local * np.where(valid, dd, 0.0)[:, None]
        self.last_scan_pts = pts_local[self.lidar_hor][:, :2]
        self._icp_update()
        # 地图更新用**估计**位姿, 而不是真值: 地图与位姿是同一套估计的产物
        est_origin = np.array([self.pose[0], self.pose[1], self.base_z]) + self._rot2d() @ np.asarray(
            self.rig.lidar.pos, dtype=float)
        dirs_est = self.lidar_local @ self._rot2d().T
        self.map.integrate(est_origin, dirs_est, hit, spec.max_m)

    def _icp_update(self) -> None:
        """把当前扫描对齐到关键帧观测 (相对运动), 抑制纯里程计的漂移。

        为什么用关键帧而不是逐帧: 雷达 10 Hz 时两帧之间只走 3 cm, 而 ICP 的解算
        噪声本身就在 1~2 cm 量级 —— 逐帧做等于把噪声按 sqrt(帧数) 累积成随机游走
        (实测把 0.05 m 的里程计误差放大到 0.12 m)。因此只在**相对关键帧走出足够
        位移**时才 ICP, 一次对齐 0.25 m 以上的位移, 信噪比才够。
        """
        pts = self.last_scan_pts
        if self._icp_key is None:
            self._icp_key = (pts.copy(), self.pose.copy())
            return
        key_pts, key_pose = self._icp_key
        moved = float(np.linalg.norm(self.pose[:2] - key_pose[:2]))
        turned = abs(_wrap(self.pose[2] - key_pose[2]))
        if moved >= KEYFRAME_M or turned >= KEYFRAME_RAD:
            key_world = self.icp._to_world(key_pts, key_pose)
            pose, resid = self.icp.align(pts, key_world, self.pose)
            if np.isfinite(resid):
                delta = pose - self.pose
                delta[2] = _wrap(delta[2])
                step = float(np.linalg.norm(delta[:2]))
                if step > 0.15:                     # 单次修正上限, 防止跳变
                    delta *= 0.15 / step
                # ICP 给出的**相对运动**是观测事实, 与地图无关 -> 用它标定里程计
                self._icp_since_calib += delta
                self.pose = self.pose + self.icp_gain * delta
                self.pose[2] = _wrap(self.pose[2])
                self.last_resid = resid
                self.icp_runs += 1
                self._calibrate()
            self._icp_key = (pts.copy(), self.pose.copy())

    def _sample_camera(self) -> None:
        o, rot = self._rig_origin(self.rig.cam.pos)
        dirs = self.cam_local @ rot.T
        d = self.caster.cast(o, dirs)
        spec = self.rig.cam
        # 深度相机噪声与距离平方成正比, 远处急剧变差
        dd = d + self.rng.normal(0, spec.sigma_k * np.square(np.maximum(d, 0.0)), size=d.shape)
        valid = (dd > spec.min_m) & (dd <= spec.max_m)
        hit = np.where(valid, dd, -1.0)
        est_origin = np.array([self.pose[0], self.pose[1], self.base_z]) + self._rot2d() @ np.asarray(
            self.rig.cam.pos, dtype=float)
        dirs_est = self.cam_local @ self._rot2d().T
        self.map.integrate(est_origin, dirs_est, hit, spec.max_m, free_samples=6)

    def _rot2d(self) -> np.ndarray:
        """估计 yaw 对应的世界 <- 本体 旋转 (本模块只做平面 + 高度)。"""
        c, s = math.cos(self.pose[2]), math.sin(self.pose[2])
        return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])

    # ---------------- 定位 ----------------

    def _match(self) -> None:
        """把当前扫描匹配到自建地图, 纠正里程计漂移。"""
        if int(self.map.grid2d().sum()) < 20:
            return                      # 地图还没成形, 匹配没有意义
        field = self.map.distance_field()
        pts = self.last_scan_pts
        # 采样点太多时抽稀 (匹配精度由地图分辨率决定, 而非点数)
        if len(pts) > 90:
            pts = pts[::max(1, len(pts) // 90)]
        pose, score = self.matcher.align(pts, self.pose, field, self.map)
        # 地图是"用估计位姿自己建的", 位姿偏了地图就整体跟着偏, 于是扫描与地图
        # 永远自洽 (实测分数长期 0.98, 而误差已经半米) —— 因此匹配**不能**当作
        # 常规纠正源, 否则只是在自己的噪声里随机游走。只有分数明显掉下来 (说明
        # 观测与地图真对不上: 被撞偏、打滑、地图里没有的新结构) 才让它介入。
        if score > MAP_ALERT_SCORE:
            self.last_score = score
            self.matches += 1
            return
        d = np.array([pose[0] - self.pose[0], pose[1] - self.pose[1],
                      _wrap(pose[2] - self.pose[2])])
        step = float(np.linalg.norm(d[:2]))
        cap = 0.25
        if step > cap:
            d *= cap / step
        self.pose = self.pose + self.gain * d
        self.pose[2] = _wrap(self.pose[2])
        self.last_score = score
        self.matches += 1

    def _calibrate(self) -> None:
        """用 ICP 观测位移 / 里程计位移 在线标定里程计尺度。

        里程计的**系统性**误差 (轮径/打滑) 表现为位移被整体放大或缩小, 走得越远
        偏得越多; 随机噪声则无法标定。参考位移必须取自**与地图无关的观测** (ICP
        的相对运动), 否则就是拿自己标定自己 —— 早期版本拿地图匹配的位移来标定,
        把尺度从 1.12 拉到 0.84, 误差反而翻倍。
        ratio 取投影 <d_icp, d_odo> / <d_odo, d_odo>, 即最小二乘意义下的最优比例;
        位移窗口太短时信噪比不够, 不标定。
        """
        d_odo = self._odo_since_calib
        d_icp = self._icp_since_calib
        n = float(np.linalg.norm(d_odo[:2]))
        if n < CALIB_WINDOW_M:
            return
        denom = float(np.dot(d_odo[:2], d_odo[:2]))
        if denom < 1e-6:
            return
        # d_icp 是 ICP 给出的**修正量** (里程计多了多少就往回拉多少), 观测到的
        # 真实位移是 d_odo + d_icp; 比例才是"里程计读数 -> 真实位移"的标定值。
        d_obs = d_odo + d_icp
        ratio = float(np.dot(d_obs[:2], d_odo[:2]) / denom)
        if not np.isfinite(ratio) or ratio <= 0.5 or ratio >= 2.0:
            return
        self.scale_factor *= (1.0 + CALIB_GAIN * (ratio - 1.0))
        self.scale_est = self.scale_factor
        self._odo_since_calib = np.zeros(3)
        self._icp_since_calib = np.zeros(3)
        self.calibrations += 1

    def _track_error(self) -> None:
        e = self.error()
        self.err_sum += e
        self.err_max = max(self.err_max, e)
        self.err_n += 1

    # ---------------- 传感器判定"看见了" ----------------

    def perceive_target(self, echo_xyz: np.ndarray, max_range: float,
                        fov_half_rad: float | None = None,
                        target_radius_m: float = 0.0
                        ) -> tuple[np.ndarray | None, float, str]:
        """识别层对本目标的读数: 观测到则返回**地图系下的测得位置**, 否则 None。

        链路是"先看 -> 再量 -> 再确认", 与真实系统同构:

            相机成像/ArUco 解码  -> 给出目标相对本体的**方位** (带角误差)
            同一帧的测距         -> 给出**距离** (误差随距离增长)
            雷达/深度回波        -> 沿该方位打过去, 回波距离要与刚才报的吻合,
                                    否则说明中间挡着别的东西 (或根本不是它)

        因此解算出来的目标位置完全由本体的观测量合成 (估计位姿 + 测得方位/距离),
        不含任何世界坐标查询。`echo_xyz` 只是让仿真器知道该让哪条射线打中什么,
        即"真值用于产生观测", 本体到手的仍然是自己的读数。
        识别层还**可能什么都不给** (太远/不在视场/被挡), 那时返回 None —— 本体
        就是不知道目标在哪, 而不是"虽然看不见但仍拿得到它的精确坐标"。
        返回 ((x, y) | None, 测得距离, 依据)。
        """
        o, _rot = self._rig_origin(self.rig.lidar.pos)
        d = np.asarray(echo_xyz, dtype=float) - o
        dist = float(np.linalg.norm(d))
        if dist > max_range:
            return None, dist, "out-of-range"
        if dist < self.rig.lidar.min_m:
            return None, dist, "too-close"
        if fov_half_rad is not None:
            # 识别靠相机: 目标必须落在相机视场内 (相对**估计**朝向)
            yaw_err = abs(_wrap(math.atan2(d[1], d[0]) - self.pose[2]))
            if yaw_err > fov_half_rad:
                return None, dist, "out-of-fov"
        # --- 识别 + 测距: 误差随距离增长 ---
        dir_t = d / dist
        dir_m = dir_t + self.rng_percept.normal(0, PERCEPT_DIR_SIGMA_RAD, size=3)
        dir_m = dir_m / float(np.linalg.norm(dir_m))
        d_m = max(dist + self.rng_percept.normal(
            0, PERCEPT_RANGE_SIGMA_M + PERCEPT_RANGE_SIGMA_K * dist), 0.0)
        # --- 回波确认: 打过去得到的距离要和目标吻合 ---
        # 射线打在目标的**表面**, 所以它天然比"到目标中心"的距离近, 近的量约等于
        # 目标半径。因此只有回波近得超出目标尺寸 (中间挡着别的东西) 或比目标还远
        # (那个方向上没有它) 才算不匹配 —— 用 `abs(hit - d_m) > tol` 会把所有有体积
        # 的目标都判成被遮挡 (实测 obs2 因此全程测不到, 推挤无从谈起)。
        hit = float(self.caster.cast(o, dir_m[None, :])[0])
        if hit < 0:
            return None, dist, "no-return"
        if hit - d_m > PERCEPT_ECHO_TOL_M:
            return None, dist, f"echo-beyond-target-{hit:.2f}"
        if d_m - hit > target_radius_m + PERCEPT_ECHO_TOL_M:
            return None, dist, f"occluded-at-{hit:.2f}"
        # --- 地图系下的目标位置: 只用估计位姿与测得量合成 ---
        org = (np.array([self.pose[0], self.pose[1], self.base_z])
               + self._rot2d() @ np.asarray(self.rig.lidar.pos, dtype=float))
        return (org + dir_m * d_m)[:2], hit, "sensor-return"

    # ---------------- 报告 ----------------

    def dump_ply(self, path: str) -> int:
        """把自建的三维地图导成 ASCII PLY 点云 (给外部工具查看/比对)。"""
        pts = self.map.occupied_centers()
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("ply\nformat ascii 1.0\n")
            f.write(f"element vertex {len(pts)}\n")
            f.write("property float x\nproperty float y\nproperty float z\n")
            f.write("end_header\n")
            for p in pts:
                f.write(f"{p[0]:.3f} {p[1]:.3f} {p[2]:.3f}\n")
        return len(pts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "body": self.body,
            "pose_est": [round(float(v), 3) for v in self.pose],
            "pose_error_m": round(self.error(), 3),
            "err_mean_m": round(self.err_sum / max(self.err_n, 1), 3),
            "err_max_m": round(self.err_max, 3),
            "scans": self.samples,
            "matches": self.matches,
            "match_score": round(self.last_score, 3),
            "plan_source": self.plan_source,
            "map": self.map.as_dict(),
        }
