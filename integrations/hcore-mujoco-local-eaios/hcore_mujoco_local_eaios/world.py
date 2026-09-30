"""MuJoCo 物理世界与三个异构本体的 Local How 驱动。

职责边界 (对齐 ADR-0006 / ADR-0021): 本模块只负责 Immediate How —— 物理步进、
本体运动、几何感知判定与本地终态观察。它**不理解** Mission / Task / Execution
Group, 不产生 RoboGuide 生命周期语义, 也不决定"该做什么"。canonical
ExecutionIntent 到本模块调用的翻译由 facade.py 完成。

四类本体的异构性刻意落在"控制栈 + 能力集"上, 而不是物理外形:
    quadruped (OpenMind OM1)      : 地面快速平台, 仅 ArUco 检测
    rover     (Robonix OS)        : 地面慢速平台, ArUco + 物体检测
    ptzcam    (ROS 2 + Nav2/MoveIt2 裸栈): 固定云台, 仅 QR 检测
    arm       (RoboOS)            : 固定基座 360° 回转臂, 仅搬运 (object.relocate)

机械臂的"回转被挡"是**几何 sweep 判定**, 不是接触动力学: 回转扫掠扇区内存在
任何实体 (含共享世界里停在扇区里的其它机器人) 即判定无法回转 —— 这是本地
Immediate How 的失败, 由本模块观察并上报, RoboGuide 侧只看到 FAILED。

回转是**双向**的: 短弧被挡时, 若回转关节无限位则改走反向长弧绕到目标; 两条
弧都被挡才判失败, 并把两侧的阻挡实体分别写进本地依据
(`local-workspace-blocked-both-directions`)。

两条弧都被挡时**不立刻判失败**: 机械臂先朝两侧各摆一次试探 (摆动只走到阻挡体
在扇区里的起始方位之前, 是试探而非撞击), 让"双向受阻"成为可见的动作, 摆完才
写 FAILED。

搬运的完成条件由 `complete_after` 决定: 默认 "grasp" (拿起即成功, 放下与归位
只是之后的收尾动作); "place" 则要求真的转到目的地并松开夹爪, 由
`local-manipulation-placed` 写终态 —— 用于"转 180° 取物 -> 转回起始方位放下"
这类完整搬运。
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

import mujoco

# H-CoRE MuJoCo 复现场景目录 (arena_roboguide.xml + hcore_nav.py 所在处)。
# 可用环境变量 HCORE_MJC_DIR 覆盖。
_HCORE_DIR = os.environ.get(
    "HCORE_MJC_DIR", "/home/sdb/sunweihao/myproject/H-CoRE-sub/hcore_mjc_repro"
)
if _HCORE_DIR not in sys.path:
    sys.path.insert(0, _HCORE_DIR)

from hcore_nav import OccupancyGrid, Occluders  # noqa: E402  依赖 H-CoRE 侧导航设施

from .slam import (  # noqa: E402  本体自带的三维感知 (深度相机 + 雷达 + 建图 + 定位)
    DepthCamSpec,
    LidarSpec,
    SensorRig,
    SlamNode,
)

# ------------------------------ 常量 ------------------------------

DETECTION_RANGE_M: dict[str, float] = {"aruco": 2.5, "object": 1.6, "qr": 8.0}
# 用自建地图定位时的到点判定: 比真值判据 (WAYPOINT_TOL_M) 宽松, 因为"自己认为到了"
# 与"真的到了"之间隔着定位误差 (实测 0.05~0.20 m), 判定太紧会永远到不了。
SLAM_WAYPOINT_TOL_M = 0.28
# 卡住恢复: 这么久没有实质位移就用当前估计位姿重规划, 最多重规划这么多次
STUCK_REPLAN_S = 4.0
MAX_REPLANS = 3
GROUND_FOV_HALF_RAD = math.radians(35.0)   # 地面平台相机水平半视场角
PTZ_AIM_TOL_RAD = 0.10                     # PTZ 精对准阈值 (rad)
WAYPOINT_TOL_M = 0.15                      # 路径点到达判定
DEFAULT_DEADLINE_S = 300.0                 # 单个 execution 的本地兜底时限

# ---------------------- PTZ 云台巡检 (observation.survey) ----------------------
# 相机是**固定基座**云台 (MJCF: ptzcam pos="-2.40 0.00 1.60", 只有 ptz_pan /
# ptz_tilt 两个转动关节, 没有平移关节), 因此"环绕场景一周"在本地只能实现为
# **云台依次扫过四个方位** —— 视角逐个掠过矩形场地的四个面, 本体并不真的平移。
# 四个方位取四个象限的中线 (-135°/-45°/+45°/+135°): 单向连续扫过 270°, 全程
# 落在 pan 限位 ±2.9 rad (±166°) 内, 不会在限位处卡死。
PTZ_SURVEY_BEARINGS_RAD: tuple[float, ...] = (-2.356, -0.785, 0.785, 2.356)
PTZ_SURVEY_LABELS: tuple[str, ...] = ("southwest", "southeast", "northeast", "northwest")
# 固定俯角 0.75 rad (43°): 相机在 1.60 m 高处, 垂直视场 (半角 27.5°) 覆盖俯角
# 16°~70°, 画面里既有相机近旁的地面 (crate1 俯角约 67°, 在下边缘内) 也有远处
# 场地 (marker6 俯角约 18°, 在上边缘内); 俯角取小了近处目标掉出画面, 取大了
# 远处地面掉出画面, 两头都必须兼顾。
PTZ_SURVEY_TILT_RAD = 0.75
PTZ_SURVEY_AIM_TOL_RAD = 0.05   # 转到该方位的到位判定, 到位后才按快门
PTZ_SURVEY_DWELL_S = 0.30       # 到位后的稳定时间, 避免云台余振造成拖影
PTZ_FOV_HALF_H_RAD = math.radians(34.5)   # 水平半视场 (fovy 55° + 4:3 画幅)
PTZ_FOV_HALF_V_RAD = math.radians(27.5)   # 垂直半视场
PTZ_SURVEY_RANGE_M = 8.0        # 相机有效量程 (与 qr 检测一致)
DEFAULT_SHOT_DIR = "artifacts/ptz-survey"   # 照片默认落盘目录

# ---------------------- 机械臂 (固定基座, 360° 回转) ----------------------
# 几何常量必须与 arena_roboguide.xml 的 arm_* 保持一致
ARM_BASE_XY = (-1.20, -0.40)   # 基座世界坐标 (MJCF arm_base pos)
ARM_SHOULDER_Z = 0.75          # 肩关节离地高度 (立柱 0.70 + 立柱内偏移 0.05)
ARM_L1 = 0.75                  # 大臂长度
ARM_L2 = 0.70                  # 小臂长度 (末端即 arm_gripper_site)
ARM_SWEEP_R_M = 1.30           # 工作空间半径: 抓取/放置的可达判定 (二连杆臂展 1.45 m)
ARM_INNER_R_M = 0.22           # 基座自身占位, 内侧不计入阻挡
# 抓取/放置的目标点是**物体顶面之上**, 不是物体中心 —— 后者会把掌心埋进
# 箱子内部 (渲染上表现为穿模)。物体放大到 0.26 m 见方后这一点尤其明显。
ARM_CRATE_HALF_M = 0.13        # crate1 半尺寸 (必须与 MJCF 的 size 一致)
ARM_GRIP_LIFT_M = 0.175        # 物体中心 -> 抓取目标点 (沿 z, 手爪局部坐标系)
ARM_PLACE_Z = ARM_CRATE_HALF_M + ARM_GRIP_LIFT_M  # 0.305: 物体底面正好落地
ARM_YAW_TOL_RAD = 0.02         # 回转对准阈值 (0.85 m 半径上约 1.7 cm 切向误差)
ARM_GRIP_TOL_M = 0.06          # 末端到位判定 (抓取/放置), 需大于伺服残余误差
ARM_FINGER_OPEN = 0.0
ARM_FINGER_CLOSED = 0.06
ARM_STOW = (-0.85, 2.30)       # 收臂姿态 (肩, 肘): 回转过程中保持, 避免扫到东西


def _stowed_sweep_radius() -> float:
    """收臂姿态下末端相对基座的水平半径 + 余量 —— 这才是"回转"真正扫过的半径。

    回转期间机械臂保持收臂姿态, 只有对准后才伸展抓取/放置。因此用它 (而不是
    臂展 1.30 m) 判定"回转会不会撞到东西"才是物理正确的: 西侧墙距基座 0.94 m,
    收臂扫不到, 不应误判为阻挡 (否则所有长弧都被墙否决, 双向回转形同虚设)。
    """
    t1, t2 = ARM_STOW
    return abs(ARM_L1 * math.cos(t1) + ARM_L2 * math.cos(t1 + t2)) + 0.05


ARM_ROTATE_R_M = _stowed_sweep_radius()   # ~0.63 m: 回转扫掠半径 (收臂)
ARM_CARRY_MARGIN_M = 0.10                 # 持物回转时, 被抓物体外缘的额外余量

# 摆动探测: 两条弧都被挡时, 机械臂**先左右各摆一次**再判失败 —— "双向受阻"在
# 画面上是可见的摆动, 而不是一次静态判定。摆动只走到"阻挡体在扇区里的起始
# 方位"之前 (留 PROBE_MARGIN 余量), 因此不会真的撞上去 (本场景的阻挡判定是
# 几何 sweep, 不是接触动力学, 撞上去只会穿模)。
ARM_PROBE_MAX_RAD = 0.70     # 单次摆动上限 (rad, 约 40°)
ARM_PROBE_MARGIN_RAD = 0.04  # 停在阻挡体之前的安全余量
ARM_PROBE_RATE = 0.45        # 摆动角速度 (rad/s): 明显慢于正常回转 1.2, 便于观察
ARM_PROBE_DWELL_S = 0.80     # 摆到极限后的停顿 (s): 让"这一侧被挡住"停留可见

# 回转阻挡物: 静态障碍挂在 worldbody 下 (只有 geom 名), 可动实体是 body
ARM_BLOCKER_GEOMS = ("obs1", "obs3", "obs4", "obs5",
                     "wall_north", "wall_south", "wall_east", "wall_west")
# obs2 已改为带 freejoint 的可推动障碍 (body), crate1 是可搬运物 (body)
ARM_BLOCKER_BODIES = ("rover", "dog", "obs2", "crate1", "teddy", "marker69",
                      "marker77", "marker90", "marker6", "marker50")

# 本地终态依据 (terminal_basis): 只能由本地观察写入
BASIS_OUT_OF_WORKSPACE = "local-reach-out-of-workspace"   # 抓取点/放置点超出臂展
BASIS_SWEEP_BLOCKED = "local-workspace-blocked"           # 选定回转方向被实体占据
BASIS_SWEEP_BLOCKED_BOTH = ("local-workspace-blocked-both-directions")  # 两侧皆被挡
BASIS_PLACED = "local-manipulation-placed"                # 夹爪在目的地真实松开
BASIS_GRASPED = "local-manipulation-grasped"              # 夹爪真实闭合并持物 (拿起即成功)
BASIS_NOT_MOVABLE = "local-object-not-movable"            # 目标没有可动自由度
BASIS_REACH_IN_WORKSPACE = "local-reach-in-workspace"     # 巡检: 目标落在臂展内
BASIS_TARGET_NOT_OBSERVED = ("local-target-not-observed")  # 巡检: 四张照片都没拍到目标

STATE_ACCEPTED = "ACCEPTED"
STATE_RUNNING = "RUNNING"
STATE_COMPLETED = "COMPLETED"
STATE_FAILED = "FAILED"
STATE_CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class RoleSpec:
    """一个本体角色的静态描述 (关节名、能力集、运动学限制)。"""

    role: str
    runtime_name: str                 # 上报给 RoboGuide 的 Local EAIOS 名字
    body: str
    slide: tuple[str, str] | None     # 地面平移关节 (x, y)
    yaw_joint: str | None
    pan_joint: str | None             # 仅 PTZ
    tilt_joint: str | None            # 仅 PTZ
    site: str                         # 感知参考点
    capability: frozenset[str]
    max_speed: float                  # m/s
    max_yaw_rate: float               # rad/s
    inflate: float                    # 栅格膨胀半径
    legs: tuple[str, ...] = ()        # 四足腿关节 (仅 quadruped)
    arm_joints: tuple[str, ...] = ()  # 机械臂 (肩, 肘) 关节, 非空即为机械臂本体
    fingers: tuple[str, str] = ()     # 夹爪滑移关节
    # 自带三维感知 (深度相机 + 雷达)。None = 该本体不建图, 感知退回几何判定。
    rig: SensorRig | None = None
    base_z: float = 0.0               # 基座离地高度 (传感器安装高度的基准, 本体自己知道)


ROLE_SPECS: dict[str, RoleSpec] = {
    "quadruped": RoleSpec(
        role="quadruped",
        runtime_name="openmind-om1",
        body="dog",
        slide=("dog_x", "dog_y"),
        yaw_joint="dog_yaw",
        pan_joint=None,
        tilt_joint=None,
        site="dog_base_link",
        capability=frozenset({"aruco"}),
        max_speed=0.55,
        max_yaw_rate=1.6,
        inflate=0.24,
        legs=("fl_upper", "fl_lower", "fr_upper", "fr_lower",
              "rl_upper", "rl_lower", "rr_upper", "rr_lower"),
        # 犬背上的雷达 + 头部的深度相机 (MJCF: dog_cam pos="0.62 0 0.20")
        rig=SensorRig(lidar=LidarSpec(pos=(0.0, 0.0, 0.20)),
                      cam=DepthCamSpec(pos=(0.62, 0.0, 0.20))),
        base_z=0.62,
    ),
    "rover": RoleSpec(
        role="rover",
        runtime_name="robonix-os",
        body="rover",
        slide=("rover_x", "rover_y"),
        yaw_joint="rover_yaw",
        pan_joint=None,
        tilt_joint=None,
        site="rover_base_footprint",
        capability=frozenset({"aruco", "object"}),
        max_speed=0.35,
        max_yaw_rate=1.0,
        inflate=0.22,
        legs=(),
        # 车顶雷达 (MJCF: rover_lidar 在 z=+0.12) + 前部深度相机 (rover_cam)
        rig=SensorRig(lidar=LidarSpec(pos=(0.0, 0.0, 0.12)),
                      cam=DepthCamSpec(pos=(0.20, 0.0, 0.05))),
        base_z=0.12,
    ),
    "ptz": RoleSpec(
        role="ptz",
        runtime_name="ros2-nav2-moveit2",
        body="ptzcam",
        slide=None,
        yaw_joint=None,
        pan_joint="ptz_pan",
        tilt_joint="ptz_tilt",
        site="ptz_camera_tf_tilt",
        capability=frozenset({"qr"}),
        max_speed=0.0,
        max_yaw_rate=0.0,
        inflate=0.0,
        legs=(),
    ),
    "arm": RoleSpec(
        role="arm",
        runtime_name="roboos",
        body="arm_base",
        slide=None,
        yaw_joint="arm_yaw",       # 回转立柱: range ±180°, 即整圈可达
        pan_joint=None,
        tilt_joint=None,
        site="arm_gripper_site",
        capability=frozenset(),    # 无感知能力: 只做搬运
        max_speed=0.0,
        max_yaw_rate=1.2,
        inflate=0.0,
        legs=(),
        arm_joints=("arm_shoulder", "arm_elbow"),
        fingers=("arm_finger_l", "arm_finger_r"),
    ),
}


def _wrap(angle: float) -> float:
    """把角度归一化到 (-pi, pi]。"""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


@dataclass
class Execution:
    """一个本地 execution 的完整本地状态。

    终态只能由本地观察写入 (`terminal_basis` 记录依据), 不允许由 Mission 完成
    或 RoboGuide 侧事件推导。
    """

    execution_id: str
    role: str
    operation: str
    parameters: dict[str, Any]
    state: str = STATE_ACCEPTED
    detail: str = ""
    terminal_basis: str = ""
    deadline_s: float = DEFAULT_DEADLINE_S
    elapsed_s: float = 0.0
    cancel_requested: bool = False
    goal: np.ndarray | None = None
    path: list[np.ndarray] = field(default_factory=list)
    target: str = ""
    kind: str = ""
    accepted_at: float = 0.0
    stage: str = ""                # 机械臂搬运阶段: align_object/grasp/align_place/place
    held: bool = False             # 物体此刻是否真的被夹爪带在末端
    yaw_goal: float | None = None  # 本轮回转的绝对目标角 (未 wrap, 可超出 ±pi)
    yaw_way: str = ""              # 本轮选定方向: "aligned" / "short" / "long" / "probe"
    yaw_way_used: str = ""         # 本次 execution 实际走过的回转方向序列 (证据)
    # 机械臂搬运的完成条件: "grasp" = 拿起即成功 (默认), "place" = 搬到目的地放下
    # 才算完成 —— 后者用于"转 180° 取物 -> 转回放下的完整搬运"任务。
    complete_after: str = "grasp"
    # 摆动探测 (两侧皆挡时, 先左右各摆一次再判失败)
    probe_goals: list[float] = field(default_factory=list)  # 待执行的摆动绝对角
    probe_blocked: str = ""        # 探测前已判定的"两侧皆挡"证据
    probe_dwell: float = 0.0       # 摆到极限后的剩余停顿时间 (s)
    # PTZ 巡检 (observation.survey)
    survey_bearings: list[float] = field(default_factory=list)  # 待扫的方位角
    survey_idx: int = 0            # 当前扫到第几个方位
    survey_dwell: float = 0.0      # 到位后的稳定计时
    survey_dir: str = ""           # 照片落盘目录
    shots: list[str] = field(default_factory=list)      # 已落盘的照片路径
    shot_seen: list[bool] = field(default_factory=list)  # 每张照片里是否看到目标


class ArenaWorld:
    """单个 MuJoCo 世界: 承载三个本体, 提供步进与几何查询。

    一个进程只持有一个世界, 三个 facade 端口共享同一份物理状态, 因此空间冲突
    (两台地面平台挤到同一区域) 是真实可观测的, 而不是各自独立的沙盒。
    """

    def __init__(self, xml_path: str) -> None:
        """加载 MJCF 并构建遮挡体与占据栅格缓存。"""
        self.model = mujoco.MjModel.from_xml_path(xml_path)
        self.data = mujoco.MjData(self.model)
        # 首次步进前先把 xpos / geom_xpos 填好, 否则几何查询会读到全零
        mujoco.mj_forward(self.model, self.data)
        self.occluders = Occluders(self.model)
        self.lock = threading.RLock()
        self.sim_time = 0.0
        self._act: dict[str, int] = {}
        for i in range(self.model.nu):
            self._act[mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, i)] = i
        self._jnt: dict[str, int] = {}
        for i in range(self.model.njnt):
            self._jnt[mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_JOINT, i)] = i
        self._grids: dict[float, OccupancyGrid] = {}
        # 机械臂初始姿态: MJCF 的 hinge 关节无法在 XML 里指定 qpos0 (默认 0 =
        # 大臂/小臂水平伸直), 伸直的臂在重力下会持续下坠, 画面上表现为"机械臂
        # 旋转着扎进地面"。这里显式置为收臂姿态并重算正运动学。
        for joint, value in zip(("arm_shoulder", "arm_elbow"), ARM_STOW):
            self.data.qpos[self.model.jnt_qposadr[self._jnt[joint]]] = value
        # 四足腿同理: hinge 默认 qpos=0 是"腿伸直", 开局位置执行器会把它一把拉到
        # 站立姿态 (-0.70), 画面上是落地瞬间抖一下。这里直接给站立姿态开局。
        for joint in ("fl_lower", "fr_lower", "rl_lower", "rr_lower"):
            self.data.qpos[self.model.jnt_qposadr[self._jnt[joint]]] = -0.70
        mujoco.mj_forward(self.model, self.data)

    def grid(self, inflate: float) -> OccupancyGrid:
        """按膨胀半径取 (缓存的) 占据栅格, 用于 A* 规划。"""
        if inflate not in self._grids:
            self._grids[inflate] = OccupancyGrid(self.model, inflate=inflate,
                                                 clearance_height=0.0)
        return self._grids[inflate]

    def set_ctrl(self, actuator: str, value: float) -> None:
        """设置执行器指令 (速度执行器为期望速度, 位置执行器为期望角度)。"""
        idx = self._act.get(actuator)
        if idx is not None:
            self.data.ctrl[idx] = value

    def qpos(self, joint: str) -> float:
        """读取关节位置。"""
        idx = self._jnt.get(joint)
        if idx is None:
            raise KeyError(f"unknown joint: {joint}")
        return float(self.data.qpos[self.model.jnt_qposadr[idx]])

    def body_xyz(self, body: str) -> np.ndarray:
        """读取 body 的世界坐标 (mujoco 3.x 用 data.xpos, 无 body_xpos)。"""
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)
        if bid < 0:
            raise KeyError(f"unknown body: {body}")
        return np.array(self.data.xpos[bid], dtype=float)

    def site_xyz(self, site: str) -> np.ndarray:
        """读取 site 的世界坐标。"""
        sid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, site)
        if sid < 0:
            raise KeyError(f"unknown site: {site}")
        return np.array(self.data.site_xpos[sid], dtype=float)

    def geom_xyz(self, geom: str) -> np.ndarray:
        """读取 geom 的世界坐标 (静态障碍挂在 worldbody 下, 只有 geom 名)。"""
        gid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, geom)
        if gid < 0:
            raise KeyError(f"unknown geom: {geom}")
        return np.array(self.data.geom_xpos[gid], dtype=float)

    def geom_radius(self, geom: str) -> float:
        """geom 的水平包围半径, 用于把障碍换算成扇区角宽度。"""
        gid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, geom)
        if gid < 0:
            raise KeyError(f"unknown geom: {geom}")
        return float(np.linalg.norm(self.model.geom_size[gid][:2]))

    def geom_footprint(self, geom: str) -> tuple[float, float]:
        """geom 的水平半尺寸 (hx, hy); 圆柱按外接方块给出。

        细长的墙体若按"外接圆半径"处理会被当成半径 3 m 的巨盘, 因此在扇区判定里
        一律用轴对齐半尺寸, 由外缘采样决定它到底伸进来多少。
        """
        gid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, geom)
        if gid < 0:
            raise KeyError(f"unknown geom: {geom}")
        size = self.model.geom_size[gid]
        if int(self.model.geom_type[gid]) == int(mujoco.mjtGeom.mjGEOM_CYLINDER):
            return float(size[0]), float(size[0])
        return float(size[0]), float(size[1])

    def joint_unlimited(self, joint: str) -> bool:
        """该转动关节是否无限位 (可连续回转, 因而存在"绕对侧长弧"的选项)。"""
        idx = self._jnt.get(joint)
        if idx is None:
            raise KeyError(f"unknown joint: {joint}")
        return not bool(self.model.jnt_limited[idx])

    def body_free(self, body: str) -> bool:
        """该 body 是否有可自由运动的自由度 (抓取后能否真实搬运)。"""
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)
        if bid < 0:
            return False
        return bool(self.model.body_dofnum[bid] >= 6)

    def set_body_xyz(self, body: str, xyz: np.ndarray) -> None:
        """通过 freejoint 直接写入物体位置 (运动学搬运) 并清零线速度。

        抓取后每个物理步把物体钉在夹爪上; 松手后交还给动力学自由下落。
        """
        bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)
        if bid < 0:
            raise KeyError(f"unknown body: {body}")
        jnt = int(self.model.body_jntadr[bid])
        if jnt < 0 or int(self.model.body_dofnum[bid]) < 6:
            raise KeyError(f"body {body} is not freely movable")
        qadr = int(self.model.jnt_qposadr[jnt])
        dadr = int(self.model.jnt_dofadr[jnt])
        self.data.qpos[qadr:qadr + 3] = np.asarray(xyz, dtype=float)
        self.data.qvel[dadr:dadr + 3] = 0.0

    def step(self, dt: float) -> None:
        """推进一个物理步 (受 model.opt.timestep 约束, 调用方负责配速)。"""
        with self.lock:
            mujoco.mj_step(self.model, self.data)
            self.sim_time += dt


class Snapshotter:
    """PTZ 相机拍照: 用场景内的 `ptz_cam` 离屏渲染并落盘 PNG。

    Renderer **必须在物理线程里创建** (EGL 上下文属于创建它的线程, 在主线程建、
    物理线程渲染会触发 eglMakeCurrent 的 EGL_BAD_ACCESS), 因此这里延迟到第一次
    `shoot()` 才建 —— 与 facade.SceneRecorder 同源同理。

    渲染不可用时 (没有 EGL 驱动) 不阻断任务: 拍照降级为"只做判定不下图",
    由 detail 里的 `(no render)` 标出, 判定本身仍是本地观察的结论。
    """

    def __init__(self, world: ArenaWorld, out_dir: str = DEFAULT_SHOT_DIR,
                 width: int = 640, height: int = 480) -> None:
        self.world = world
        self.out_dir = out_dir
        self._width = width
        self._height = height
        self._renderer = None
        self._cam: mujoco.MjvCamera | None = None
        self._opt: mujoco.MjvOption | None = None
        self._disabled = False

    def _camera(self) -> mujoco.MjvCamera:
        """惰性构造固定相机与渲染选项 (任务目标在 geom group 3, 必须全部打开)。"""
        if self._cam is None:
            cam = mujoco.MjvCamera()
            cam.type = mujoco.mjtCamera.mjCAMERA_FIXED
            cam.fixedcamid = mujoco.mj_name2id(
                self.world.model, mujoco.mjtObj.mjOBJ_CAMERA, "ptz_cam")
            if cam.fixedcamid < 0:
                raise SystemExit("场景内没有名为 'ptz_cam' 的相机")
            self._cam = cam
            opt = mujoco.MjvOption()
            for grp in range(mujoco.mjNGROUP):
                opt.geomgroup[grp] = 1
            self._opt = opt
        return self._cam

    def shoot(self, name: str, out_dir: str | None = None) -> str | None:
        """拍一张并落盘, 返回文件路径; 渲染不可用返回 None。

        必须在**物理线程**里调用 (见类注释)。渲染前持 world.lock, 与 mj_step 互斥。
        """
        if self._disabled:
            return None
        try:
            import imageio.v2 as imageio
        except ImportError:  # pragma: no cover - 环境相关
            self._disabled = True
            return None
        if self._renderer is None:
            try:
                self._renderer = mujoco.Renderer(self.world.model,
                                                 height=self._height, width=self._width)
            except Exception as exc:  # pragma: no cover - 环境相关
                self._disabled = True
                print(f"[shot] 离屏渲染不可用, 拍照降级为纯判定 "
                      f"(需 MUJOCO_GL=egl 且本机有 EGL 驱动): {exc}", file=sys.stderr)
                return None
        with self.world.lock:
            self._renderer.update_scene(self.world.data, camera=self._camera(),
                                        scene_option=self._opt)
            frame = self._renderer.render()
        directory = out_dir or self.out_dir
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{name}.png")
        imageio.imwrite(path, frame)
        return path


class ExecutionTracer:
    """把每个 execution 的**逐条运行轨迹**落成 JSONL 日志。

    为什么要它: 节点 stdout 是空的, 控制面只读得到终态, 而过程的明细
    (`navigate: 1.23 m to next waypoint` 之类) 只存在于本地 facade 进程的内存里,
    进程一停就再也查不到。这里按事件落盘, 一行一个事件, 三种事件:

        session-start  本轮 facade 启动 (多轮追加时用它分隔)
        accepted       登记 execution: operation / parameters / 仿真时刻
        sample         运行中的周期采样 (按**仿真时间**节流): state / detail / 姿态
        terminal       本地终态: state / detail / terminal_basis / 耗时 / 照片

    采样按仿真时间而不是墙钟, 因此倍速不影响轨迹密度: 一个 20 s 的导航任务在
    --speed 8 下同样留下约 20 条采样, 不会因为它跑得快就只剩两条。
    """

    def __init__(self, path: str, role: str, interval_s: float = 1.0) -> None:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self.path = path
        self.role = role
        self.interval_s = max(0.05, float(interval_s))
        self._lock = threading.Lock()
        self._fh = open(path, "a", encoding="utf-8")   # 追加: 多轮运行可追溯
        self._next_t: dict[str, float] = {}
        self._emit({"event": "session-start", "role": role,
                    "wall": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "ts_ms": int(time.time() * 1000),
                    "interval_s": self.interval_s})

    def _emit(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()   # 进程可能被 kill, 不 flush 会丢尾部事件

    def emit(self, kind: str, ex: Execution, world: ArenaWorld,
             **extra: Any) -> None:
        """写一条事件; 调用方可能来自 HTTP 线程与物理线程, 故内部加锁。"""
        record = {
            "event": kind,
            "role": self.role,
            "execution_id": ex.execution_id,
            "operation": ex.operation,
            "t_sim": round(world.sim_time, 3),
            "wall": time.strftime("%H:%M:%S"),
            # epoch 毫秒: 与控制面侧墙钟做差得到下发/反馈延迟 (wall 只到秒, 不够)
            "ts_ms": int(time.time() * 1000),
            "state": ex.state,
            "detail": ex.detail,
            "elapsed_s": round(ex.elapsed_s, 2),
        }
        record.update(extra)
        self._emit(record)

    def due(self, ex: Execution, world: ArenaWorld) -> bool:
        """该 execution 是否到了下一次采样点 (按仿真时间节流)。"""
        if world.sim_time < self._next_t.get(ex.execution_id, -1e9):
            return False
        self._next_t[ex.execution_id] = world.sim_time + self.interval_s
        return True

    def close(self) -> None:
        """收尾关闭文件句柄。"""
        with self._lock:
            try:
                self._fh.close()
            except Exception:  # pragma: no cover - 收尾兜底
                pass


class RoleRuntime:
    """一个本体的执行运行时: 把本地 execution 翻译成物理动作并观察终态。

    它是 Local EAIOS 的"小脑": 接受 facade 下发的语义参数 (destination /
    expected), 自己做路径规划、运动控制与感知判定, 并只上报本地观察到的终态。
    """

    def __init__(self, world: ArenaWorld, spec: RoleSpec,
                 shots: Snapshotter | None = None,
                 tracer: ExecutionTracer | None = None,
                 slam: bool = True, slam_seed: int = 0) -> None:
        """绑定世界与角色规格; shots 为相机的拍照设施, tracer 为轨迹日志。

        slam=True 时本体用自带的深度相机 + 雷达建三维地图并**自己估计位姿**:
        导航的闭环控制、到点判定与"看见了没有"都只用估计量, 不再直接读世界真值
        (真值只用于产生观测与事后评估误差)。
        """
        self.world = world
        self.spec = spec
        self._shots = shots
        self._tracer = tracer
        self.slam: SlamNode | None = None
        # 导航卡住检测的状态 (每个 navigate execution 开始时重置)
        self._nav_stuck_t = 0.0
        self._nav_replans = 0
        self._nav_last_xy = np.zeros(2)
        if slam and spec.rig is not None and spec.slide is not None:
            self.slam = SlamNode(world, spec.body, spec.yaw_joint, spec.slide,
                                 spec.rig, seed=slam_seed, base_z=spec.base_z)
        self.executions: dict[str, Execution] = {}
        self.active: str | None = None
        self.pending: list[str] = []
        self.lock = threading.RLock()
        self._gait_phase = 0.0
        self._arm_hold_q: tuple[float, ...] | None = None   # 机械臂保持锁存
        # 抓取成功后的收尾序列: 原地放下 -> 收臂 -> 转回初始朝向 (让下一次搬运
        # 仍能演示一次完整回转, 而不是"已经对准 -> 瞬间完成")
        self._settle: Execution | None = None
        self._settle_stage: str = ""
        self._settle_t: float = 0.0

    # ---------------- 状态查询 ----------------

    def xy(self) -> np.ndarray:
        """本体当前的平面位置 (**世界坐标**)。

        不能读滑动关节的 qpos: 它的零位就是 body 的 `pos`, qpos 只是**相对该
        零位的偏移** (实测: rover pos=(-0.90,1.30) 时 qpos=(0,0))。直接读 qpos
        会把规划起点、到点判定与朝向误差全部算进偏移空间, 与占据栅格错位,
        表现为"车/狗整体平移了一个初始位置" —— 目标看起来到达了, 实际没到。
        """
        w = self.world
        if self.spec.slide is None:
            return w.site_xyz(self.spec.site)[:2]
        return w.body_xyz(self.spec.body)[:2]

    def yaw(self) -> float:
        """本体当前朝向。"""
        if self.spec.yaw_joint is None:
            return 0.0
        return self.world.qpos(self.spec.yaw_joint)

    # ---------------- 自己估计的位姿 (SLAM) ----------------

    def xy_est(self) -> np.ndarray:
        """本体**自己认为的**平面位置; 没有 SLAM 时退化为真值。"""
        if self.slam is not None:
            return self.slam.xy_est()
        return self.xy()

    def yaw_est(self) -> float:
        """本体**自己认为的**朝向; 没有 SLAM 时退化为真值。"""
        if self.slam is not None:
            return self.slam.yaw_est()
        return self.yaw()

    def tol(self) -> float:
        """到点判定: 用自建地图定位时放宽, 否则"自己认为到了"永远不成立。"""
        return SLAM_WAYPOINT_TOL_M if self.slam is not None else WAYPOINT_TOL_M

    def eye(self) -> np.ndarray:
        """感知参考点 (相机) 的世界坐标。"""
        return self.world.site_xyz(self.spec.site)

    def _pose(self) -> dict[str, Any]:
        """轨迹采样用的本体姿态: 地面平台给 (x, y, yaw), 云台给 (pan, tilt),
        机械臂给回转角 —— 各本体能动的自由度不同, 轨迹里就记各自的那几个。"""
        w = self.world
        if self.spec.slide is not None:
            xy = self.xy()
            pose: dict[str, Any] = {
                "pos": [round(float(xy[0]), 3), round(float(xy[1]), 3)],
                "yaw": round(self.yaw(), 3)}
            if self.slam is not None:
                e = self.slam.xy_est()
                pose["pos_est"] = [round(float(e[0]), 3), round(float(e[1]), 3)]
                pose["yaw_est"] = round(self.slam.yaw_est(), 3)
                pose["pose_err_m"] = round(self.slam.error(), 3)
            return pose
        if self.spec.pan_joint is not None:
            return {"pan": round(w.qpos(self.spec.pan_joint), 3),
                    "tilt": round(w.qpos(self.spec.tilt_joint), 3)}
        if self.spec.arm_joints and self.spec.yaw_joint:
            return {"arm_yaw": round(w.qpos(self.spec.yaw_joint), 3)}
        return {}

    def busy(self) -> bool:
        """本机此刻是否有正在进行的动作 (含搬运成功后的收尾序列)。

        录像用它判断"这一段是不是空镜头": 控制面在 Mission 之间要重 Match /
        Propose / Commit, 这段时间为**秒级墙钟**, 世界完全静止 —— 录进去就是
        没有信息量的几秒黑屏般的静止画面。
        """
        return self.active is not None or self._settle is not None

    def moving(self) -> bool:
        """本体当前是否在移动 (用于 cancel 的真实停机判定)。"""
        w = self.world
        if self.spec.arm_joints and self.spec.yaw_joint:
            # 固定基座臂: 只有回转立柱会动
            v = w.data.qvel[w.model.jnt_dofadr[w._jnt[self.spec.yaw_joint]]]
            return abs(float(v)) > 0.02
        if self.spec.slide is None:
            return False
        vx = w.data.qvel[w.model.jnt_dofadr[w._jnt[self.spec.slide[0]]]]
        vy = w.data.qvel[w.model.jnt_dofadr[w._jnt[self.spec.slide[1]]]]
        return math.hypot(vx, vy) > 0.03

    # ---------------- 感知 ----------------

    def can_serve(self, kind: str) -> bool:
        """本本体是否具备该类感知能力 (Local capability 判定)。"""
        return kind in self.spec.capability

    def detect(self, kind: str, target: str) -> bool:
        """判定目标此刻是否真的被本本体观测到 (量程 + 视场 + 视线遮挡)。

        带自带传感器的地面平台不使用几何真值判定, 而是**问自己的传感器**: 识别
        层给出目标的方位与距离, 再沿该方位打一条射线, 回波距离与之吻合才算"看见
        了" —— 被挡住、超出量程、不在相机视场里都拿不到读数。目标的世界坐标因此
        是本体**量出来的**, 而不是查出来的。
        """
        if not self.can_serve(kind):
            return False
        w = self.world
        if self.slam is not None:
            try:
                tgt3 = w.body_xyz(target)[:3]
            except KeyError:
                return False
            # `tgt3` 只用于让仿真器把这条射线**真实地打在它身上**; 本体到手的是自己
            # 的识别方位 + 测距, 目标位置由 perceive_target 合成。**看不到时它什么都
            # 拿不到**, 而不是像直接读坐标那样永远知道。
            pos, _dist, _why = self.slam.perceive_target(
                tgt3, DETECTION_RANGE_M.get(kind, 2.0), GROUND_FOV_HALF_RAD)
            return pos is not None
        eye = self.eye()
        try:
            tgt = w.body_xyz(target)
        except KeyError:
            return False
        d = tgt - eye
        dist = float(np.linalg.norm(d))
        if dist > DETECTION_RANGE_M.get(kind, 2.0):
            return False
        if self.spec.slide is not None:
            yaw_err = abs(_wrap(math.atan2(d[1], d[0]) - self.yaw()))
            if yaw_err > GROUND_FOV_HALF_RAD:
                return False
        if w.occluders.blocked(eye, tgt):
            return False
        return True

    # ---------------- execution 管理 ----------------

    def submit(self, operation: str, parameters: dict[str, Any]) -> Execution:
        """登记一个新的本地 execution, 返回带幂等 handle 的记录。

        登记与初始化在同一把锁内完成, 避免物理线程在 `_begin` 之前读到尚未
        填好路径的 execution 而误判"已到达"。
        """
        ex = Execution(
            execution_id=f"{self.spec.role}-" + uuid.uuid4().hex[:12],
            role=self.spec.role,
            operation=operation,
            parameters=dict(parameters),
            deadline_s=float(parameters.get("deadline_s", DEFAULT_DEADLINE_S)),
            accepted_at=self.world.sim_time,
        )
        with self.lock:
            self.executions[ex.execution_id] = ex
            self.pending.append(ex.execution_id)
            if self.active is None:
                self._promote_locked()
            else:
                ex.detail = "queued behind current local execution"
        if self._tracer is not None:
            self._tracer.emit("accepted", ex, self.world,
                              parameters=dict(parameters))
        return ex

    def cancel(self, execution_id: str) -> bool:
        """请求取消; 只持久化请求, 真正 CANCELLED 由 tick 观察到停机后写入。"""
        with self.lock:
            ex = self.executions.get(execution_id)
            if ex is None:
                return False
            ex.cancel_requested = True
        if self._tracer is not None:
            self._tracer.emit("cancel-requested", ex, self.world)
        return True

    def _promote_locked(self) -> None:
        """把队列中下一个 execution 初始化为当前执行对象。

        若某个 execution 在 `_begin` 阶段就被本地判失败, 则跳过它继续推进队列,
        保证一次本地拒绝不会堵死后续 execution。
        """
        while self.pending:
            nid = self.pending.pop(0)
            ex = self.executions.get(nid)
            if ex is None:
                continue
            self._begin(ex)
            if ex.state in (STATE_ACCEPTED, STATE_RUNNING):
                self.active = nid
                return
        self.active = None

    def _plan_to(self, start: np.ndarray, goal: np.ndarray) -> list[np.ndarray] | None:
        """从 start 规划到 goal: 优先用**自己建的三维地图**的二维投影。

        自己的地图还没覆盖到目标 (或缝隙太多) 时才回退到仿真栅格, 用了哪张图记进
        `plan_source`, 让"这次导航是照自己的地图走的"成为可核对的事实。
        """
        path: list[np.ndarray] | None = None
        if self.slam is not None:
            path = self.slam.planner.plan(start, goal)
            self.slam.plan_source = "self-map" if path else "world-fallback"
        if not path:
            path = self.world.grid(self.spec.inflate).plan(start, goal)
            if path and self.slam is not None:
                self.slam.plan_source = "world-fallback"
        return path

    def _begin(self, ex: Execution) -> None:
        """根据 operation 解析语义参数, 失败则立刻写入本地 FAILED。"""
        if ex.operation == "mobility.navigate":
            dest = str(ex.parameters.get("destination", ""))
            goal = self._resolve_destination(dest)
            if goal is None:
                self._finish(ex, STATE_FAILED, f"unknown destination: {dest}",
                             "local-resolution-failed")
                return
            ex.goal = goal
            self._nav_stuck_t = 0.0
            self._nav_replans = 0
            self._nav_last_xy = self.xy_est()
            path = self._plan_to(self.xy_est(), goal)
            if not path:
                self._finish(ex, STATE_FAILED, f"no path to {dest}",
                             "local-planner-no-path")
                return
            ex.path = list(path)
            src = self.slam.plan_source if self.slam is not None else "world-grid"
            ex.detail = (f"navigate -> {dest} ({len(ex.path)} waypoints, "
                         f"planned on {src})")
        elif ex.operation == "observation.verify":
            expected = str(ex.parameters.get("expected", ""))
            kind, target = self._parse_expected(expected)
            if kind is None:
                self._finish(ex, STATE_FAILED, f"unparsable expected: {expected}",
                             "local-resolution-failed")
                return
            if not self.can_serve(kind):
                self._finish(ex, STATE_FAILED,
                             f"capability mismatch: {self.spec.role} cannot sense {kind}",
                             "local-capability-rejected")
                return
            ex.kind, ex.target = kind, target
            ex.detail = f"verify {expected} (kind={kind}, target={target})"
        elif ex.operation == "observation.survey":
            target = str(ex.parameters.get("target", ""))
            if self.spec.pan_joint is None:
                self._finish(ex, STATE_FAILED,
                             f"{self.spec.role} has no pan/tilt camera",
                             "local-capability-rejected")
                return
            try:
                self.world.body_xyz(target)
            except KeyError:
                self._finish(ex, STATE_FAILED, f"unknown target: {target}",
                             "local-resolution-failed")
                return
            views = int(ex.parameters.get("views", len(PTZ_SURVEY_BEARINGS_RAD)) or 0)
            views = max(1, min(views, len(PTZ_SURVEY_BEARINGS_RAD)))
            ex.target = target
            ex.survey_bearings = list(PTZ_SURVEY_BEARINGS_RAD[:views])
            ex.survey_dir = str(ex.parameters.get("out_dir")
                                or (self._shots.out_dir if self._shots
                                    else DEFAULT_SHOT_DIR))
            ex.detail = (f"survey: {views} views -> {target} "
                         f"(shots -> {ex.survey_dir})")
        elif ex.operation == "object.relocate":
            obj = str(ex.parameters.get("object", ""))
            dest = str(ex.parameters.get("destination", ""))
            if not self.spec.arm_joints:
                self._finish(ex, STATE_FAILED, f"{self.spec.role} has no manipulator",
                             "local-capability-rejected")
                return
            try:
                obj_pos = self.world.body_xyz(obj)
            except KeyError:
                self._finish(ex, STATE_FAILED, f"unknown object: {obj}",
                             "local-resolution-failed")
                return
            if not self.world.body_free(obj):
                self._finish(ex, STATE_FAILED,
                             f"object {obj} has no movable DOF in this world",
                             BASIS_NOT_MOVABLE)
                return
            goal = self._resolve_destination(dest)
            if goal is None:
                self._finish(ex, STATE_FAILED, f"unknown destination: {dest}",
                             "local-resolution-failed")
                return
            # 固定基座臂: 抓取点与放置点都必须落在臂展内, 否则本地直接判失败
            if self._arm_ik(self._grasp_point(obj_pos)) is None:
                self._finish(ex, STATE_FAILED,
                             f"{obj} out of workspace (radius {self._arm_radius(obj_pos):.2f} m)",
                             BASIS_OUT_OF_WORKSPACE)
                return
            if self._arm_ik(np.array([goal[0], goal[1], ARM_PLACE_Z])) is None:
                self._finish(ex, STATE_FAILED,
                             f"destination {dest} out of workspace",
                             BASIS_OUT_OF_WORKSPACE)
                return
            # 完成条件: 默认"拿起即成功"; complete_after=place 时要求真的搬到
            # 目的地放下才算完成 (完整搬运: 转 180° 取物 -> 转回起始方位放下)。
            ex.complete_after = ("place" if str(ex.parameters.get(
                "complete_after", "grasp")).lower() == "place" else "grasp")
            ex.target, ex.goal, ex.stage = obj, goal, "align_object"
            ex.detail = (f"relocate {obj} -> {dest} "
                         f"(complete after {ex.complete_after})")
        else:
            self._finish(ex, STATE_FAILED, f"unsupported operation: {ex.operation}",
                         "local-operation-unsupported")
            return
        ex.state = STATE_RUNNING

    @staticmethod
    def _parse_expected(expected: str) -> tuple[str | None, str]:
        """把语义 `expected` 解析成 (感知类别, 目标 body 名)。

        支持 `marker6.exists` / `qr6.detected` / `teddy.exists` 三种写法,
        对应 H-CoRE 场景里的 ArUco / QR / 物体三类目标。
        """
        name = expected.split(".")[0].strip()
        if name.startswith("qr"):
            return "qr", name
        if name == "teddy":
            return "object", name
        if name.startswith("marker"):
            return "aruco", name
        return None, ""

    def _resolve_destination(self, dest: str) -> np.ndarray | None:
        """把语义目的地名解析成世界坐标 (区域中心或目标 body 位置)。"""
        w = self.world
        spots = {
            "zone_south": np.array([0.0, -2.20]),
            "zone_mid": np.array([0.0, 0.10]),
            "zone_north": np.array([0.0, 2.30]),
            # ---- 机械臂回转扇区语义点 ----
            # 全部以基座 B=(-1.20,-0.40) 为极点给出 (r, 方位), 由 geom_audit.py 复核。
            # 第 0 帧的双向遮挡直接由 MJCF 给死:
            #   obs2 (可推障碍) 方位 +45°, r=0.90 -> 封住顺弧 (短弧, 0 -> +180)
            #   dog  (四足)     方位 -45°, r=0.92 -> 封住逆弧 (长弧, 0 -> -180)
            #   crate1 (目标物) 方位 +175.9°, r=0.70 -> 在两者夹角**外**
            # 臂初始 yaw=0 -> 末端指向方位 0°, 落在 obs2 与 dog 的夹角内; 伸向
            # crate1 时顺弧必穿 obs2、逆弧必穿 dog —— 双向遮挡在第 0 帧就成立。
            "arm_pick_spot": np.array([-1.90, -0.35]),
            # 放下点 = 臂的**初始朝向**那一侧 (方位 0°, r=0.55): "转 180° 到后方
            # 取物 -> 转回起始方位放下"是一个完整搬运, 而不是"拿起即成功"。
            # 取 r=0.55 而不是更大: 持物回转的扫掠半径 = 末端半径 + 0.10, 末端越远
            # 扇区越大, 越容易被刚推出环带的 obs2 重新挡住。
            "arm_home_drop": np.array([-0.65, -0.40]),
            # 狗的扇区内停机点 (MJCF dog pos): 方位 -45°, r=0.92。足迹覆盖
            # [-88.7°,-18.6°], 整段落在逆弧 (长弧) 内, 只封长弧, 不碰短弧。
            "arm_block_far_spot": np.array([-0.55, -1.05]),
            # 狗的扇区外停机点: 同一条 -45° 射线上 r=2.12, 环带外, 进出都不穿障。
            "arm_dog_out_spot": np.array([0.30, -1.90]),
            # rover 推挤后撤离点: +45° 方向的外侧 r=2.20 —— 朝**远离**障碍的一侧
            # 撤离, 不会回头撞上刚顶过的障碍。
            "arm_clear_spot": np.array([0.356, 1.156]),
            # 顶障碍**出**扇区的站位: obs2 的西北外侧 (方位 +114°, r=1.75)。
            # 必须从西北朝东南顶: 朝南顶会把障碍推向基座 (r 反而变小), 朝北顶则
            # 会让 rover 自己先撞进环带。
            "arm_push_out_stage": np.array([-1.90, 1.20]),
            # 顶出行程的**终点**: 推进量 = 行程 - 接触间隙 0.51 m, 所以终点必须落在
            # 障碍**初始位置之外**, 不能写 "obs2" 本身 —— 写它自己的话 rover 顶到
            # 障碍原位就停, 推进量被钳死在 0.51 m (实测只有 0.45 m), 障碍还留在
            # 环带里, 于是只能再绕回去顶第二次。这里沿顶推方向多给约 2.04 m 行程,
            # 一次冲撞就把 obs2 从 r=0.90 顶到 r~1.40 (持物回转要求 > 1.16)。
            # 终点自身也在扇区外 (r=1.13), rover 不会把自己留成第二个阻挡体。
            "arm_push_out_clear": np.array([-0.19, 0.10]),
        }
        if dest in spots:
            return spots[dest]
        # body 名目的地: 这是**任务层给出的先验** (语义地图上的地标), 不是本体此刻
        # 的识别结果 —— "去 obs2 那儿"等价于人在地图上指了一个点。它与"看见它没有"
        # 是两件事, 后者由 perceive_target 负责。若要更严格, 这里应换成"上次观测到
        # 它的位置 + 地图传播的不确定性", 而不是取精确坐标。
        try:
            return w.body_xyz(dest)[:2]
        except KeyError:
            return None

    def _finish(self, ex: Execution, state: str, detail: str, basis: str) -> None:
        """写入本地终态及其依据, 并释放当前槽位。"""
        ex.state = state
        ex.detail = detail
        ex.terminal_basis = basis
        if self.active == ex.execution_id:
            self.active = None
        if self._tracer is not None:
            extra: dict[str, Any] = {"terminal_basis": basis}
            if ex.shots:      # 巡检: 把四张照片路径一起记进轨迹
                extra["shots"] = list(ex.shots)
            if ex.shot_seen:
                extra["shot_seen"] = list(ex.shot_seen)
            if self.slam is not None:   # 终态时刻的"我自己认为我在哪"
                extra["slam"] = self.slam.as_dict()
            self._tracer.emit("terminal", ex, self.world, **extra)

    # ---------------- 每步推进 ----------------

    def tick(self, dt: float) -> None:
        """推进本角色一个物理步: 运动控制、感知判定、超时与取消处理。"""
        with self.lock:
            # 自带传感器: 采样 -> 建图 -> 定位, 必须在控制之前, 否则这一步的控制
            # 用的是上一步的位姿估计。
            if self.slam is not None:
                self.slam.step(dt)
            # 被夹爪带走的物体必须每步跟随 —— 包括**已经写入终态**的 execution。
            # "拿起即成功"之后物体仍挂在末端, 若只在 active 时搬运, 终态一写入它
            # 就会凭空掉下去 (crate1 有 freejoint, 会自由落体)。
            for held_ex in self.executions.values():
                if held_ex.held:
                    self._arm_carry(held_ex)
            if self.active is None and self.pending:
                self._promote_locked()
            ex = self.executions.get(self.active) if self.active else None
            if ex is None:
                # 收尾序列与"空闲保持"互斥: _hold 会把控制指令锁回当前关节角,
                # 若两者在同一物理步里先后执行, 收尾的回转指令会被立刻覆盖掉。
                if self._settle is not None:
                    self._tick_settle(dt)
                else:
                    self._hold()
                return
            ex.elapsed_s += dt

            if ex.cancel_requested:
                self._hold()
                if not self.moving():
                    self._finish(ex, STATE_CANCELLED, "stopped by local cancel",
                                 "local-stop-observed")
                return

            if ex.operation == "mobility.navigate":
                self._tick_navigate(ex, dt)
            elif ex.operation == "observation.verify":
                self._tick_verify(ex, dt)
            elif ex.operation == "observation.survey":
                self._tick_survey(ex, dt)
            elif ex.operation == "object.relocate":
                self._tick_relocate(ex, dt)

            if ex.state == STATE_RUNNING and ex.elapsed_s > ex.deadline_s:
                self._finish(ex, STATE_FAILED,
                             f"local deadline {ex.deadline_s}s exceeded",
                             "local-timeout")
            # 周期采样: 终态已由 _finish 单独落盘, 这里只记运行中的轨迹
            if (self._tracer is not None
                    and ex.state in (STATE_ACCEPTED, STATE_RUNNING)
                    and self._tracer.due(ex, self.world)):
                self._tracer.emit("sample", ex, self.world, **self._pose())

    def _hold(self) -> None:
        """无活动 execution 时刹车保持原地 (速度指令归零即制动)。"""
        w = self.world
        if self.spec.slide is not None:
            w.set_ctrl(f"{self.spec.slide[0]}_act", 0.0)
            w.set_ctrl(f"{self.spec.slide[1]}_act", 0.0)
            w.set_ctrl(f"{self.spec.yaw_joint}_act", 0.0)
            self._gait(0.0, 0.0)
        if self.spec.pan_joint is not None:
            w.set_ctrl("ptz_pan_act", 0.0)
            w.set_ctrl("ptz_tilt_act", 0.0)
        if self.spec.arm_joints:
            self._arm_hold()

    def _tick_navigate(self, ex: Execution, dt: float) -> None:
        """沿 A* 路径逐点推进; 到位即写入本地 COMPLETED。

        有 SLAM 时, 反馈量全部取自**自己估计的位姿** (`xy_est` / `yaw_est`), 因此
        闭环里流的是"我认为我在哪", 而不是世界真值。
        """
        cur = self.xy_est()
        tol = self.tol()
        while ex.path and float(np.linalg.norm(ex.path[0] - cur)) < tol:
            ex.path.pop(0)
            cur = self.xy_est()
        # 卡住检测。用**估计位姿**闭环时, 定位偏差会让本体"以为"自己在推进, 实则顶在
        # 墙上或障碍上 (实测: 单独派 rover 直冲顶出点时, 0.13 m 的偏差就让它把路点判成
        # 已到达, 然后原地耗到 300 s 超时)。真实平台同样靠"没有实质位移"触发恢复:
        # 这里先用当前估计位姿重规划, 多次仍无进展才判失败。
        moved = float(np.linalg.norm(cur - self._nav_last_xy))
        if moved > 0.05:
            self._nav_stuck_t = 0.0
            self._nav_last_xy = cur.copy()
        else:
            self._nav_stuck_t += dt
        if self._nav_stuck_t > STUCK_REPLAN_S:
            self._nav_stuck_t = 0.0
            self._nav_replans += 1
            if self._nav_replans > MAX_REPLANS:
                self._hold()
                self._finish(ex, STATE_FAILED,
                             f"no progress after {MAX_REPLANS} replans "
                             f"(stuck {STUCK_REPLAN_S}s each)",
                             "local-stuck-no-progress")
                return
            replanned = self._plan_to(cur, ex.goal)
            if not replanned:
                self._hold()
                self._finish(ex, STATE_FAILED, "no path on replan",
                             "local-planner-no-path")
                return
            ex.path = list(replanned)
            ex.detail += f" [replan #{self._nav_replans}]"
        if not ex.path:
            self._hold()
            self._finish(ex, STATE_COMPLETED, "destination reached",
                         "local-odometry-arrival")
            return
        wp = ex.path[0]
        d = wp - cur
        dist = float(np.linalg.norm(d))
        yaw_err = _wrap(math.atan2(d[1], d[0]) - self.yaw_est())
        yaw_cmd = max(-self.spec.max_yaw_rate,
                      min(self.spec.max_yaw_rate, 3.0 * yaw_err))
        # 朝向偏差过大时先转向, 避免横移; 但已经贴近路点时方位角会剧烈抖动,
        # 若仍要求"先转向"会永久停死在离目标 ~0.2 m 处 (到点判定 0.15 m 永远
        # 不成立)。故近距离直接放行。
        speed = self.spec.max_speed if (abs(yaw_err) < 0.45 or dist < 0.40) else 0.0
        # 世界系执行器必须按**本体自己认为的朝向**分解速度: 用 yaw_est 而不是 yaw。
        # 真实全向底盘接受的是**本体系**指令, 由底盘控制器按它自己认为的朝向换算成
        # 轮速 —— 本体不可能按真值朝向发指令。此处用估计朝向, 朝向误差才会真的进入
        # 闭环 (表现为轨迹侧的偏移), 而不是被悄悄抹掉。
        self.world.set_ctrl(f"{self.spec.slide[0]}_act", speed * math.cos(self.yaw_est()))
        self.world.set_ctrl(f"{self.spec.slide[1]}_act", speed * math.sin(self.yaw_est()))
        self.world.set_ctrl(f"{self.spec.yaw_joint}_act", yaw_cmd)
        self._gait(speed, dt)
        ex.detail = f"navigate: {dist:.2f} m to next waypoint"

    def _tick_verify(self, ex: Execution, dt: float) -> None:
        """执行观测: 地面平台原地扫掠, PTZ 精对准; 观测成功才 COMPLETED。"""
        if self.detect(ex.kind, ex.target):
            self._hold()
            self._finish(ex, STATE_COMPLETED,
                         f"observed {ex.target} as {ex.kind}",
                         "local-sensor-observed")
            return
        if self.spec.pan_joint is not None:
            self._aim_at(ex.target)
        elif self.spec.yaw_joint is not None:
            # 地面平台: 原地慢速扫掠, 直到进入视场或被遮挡超时
            self.world.set_ctrl(f"{self.spec.slide[0]}_act", 0.0)
            self.world.set_ctrl(f"{self.spec.slide[1]}_act", 0.0)
            self.world.set_ctrl(f"{self.spec.yaw_joint}_act", 0.6)
            self._gait(0.0, dt)
        ex.detail = f"verifying {ex.target}: not yet observed"

    def _aim_at(self, target: str) -> None:
        """PTZ: 用 P 控制把 pan/tilt 对准目标; 对准误差进入阈值即算精对准。"""
        w = self.world
        tgt = w.body_xyz(target)
        eye = self.eye()
        d = tgt - eye
        pan_des = math.atan2(d[1], d[0])
        horiz = math.hypot(d[0], d[1])
        tilt_des = -math.atan2(d[2], horiz)
        pan_err = _wrap(pan_des - w.qpos("ptz_pan"))
        tilt_err = _wrap(tilt_des - w.qpos("ptz_tilt"))
        w.set_ctrl("ptz_pan_act", max(-1.2, min(1.2, 3.0 * pan_err)))
        w.set_ctrl("ptz_tilt_act", max(-1.2, min(1.2, 3.0 * tilt_err)))

    # ---------------- PTZ 云台巡检 (observation.survey) ----------------

    def _tick_survey(self, ex: Execution, dt: float) -> None:
        """云台依次转过四个方位: 每到一个方位按下一次快门, 全部拍完做可达性判定。

        "环绕场景一周"的固定基座实现: 本体不动, 靠 pan 单向扫过 270°, 视角逐个
        掠过矩形场地的四个面; 每个方位到位并稳定后才拍照, 避免余振造成的拖影。
        """
        if ex.survey_idx >= len(ex.survey_bearings):
            self._hold()
            self._finish_survey(ex)
            return
        w = self.world
        bearing = ex.survey_bearings[ex.survey_idx]
        pan_err = _wrap(bearing - w.qpos("ptz_pan"))
        tilt_err = _wrap(PTZ_SURVEY_TILT_RAD - w.qpos("ptz_tilt"))
        w.set_ctrl("ptz_pan_act", max(-1.2, min(1.2, 3.0 * pan_err)))
        w.set_ctrl("ptz_tilt_act", max(-1.2, min(1.2, 3.0 * tilt_err)))
        total = len(ex.survey_bearings)
        if abs(pan_err) > PTZ_SURVEY_AIM_TOL_RAD or abs(tilt_err) > PTZ_SURVEY_AIM_TOL_RAD:
            ex.detail = (f"survey view {ex.survey_idx + 1}/{total}: pan "
                         f"{math.degrees(w.qpos('ptz_pan')):+.0f}° -> "
                         f"{math.degrees(bearing):+.0f}°")
            return
        # 已到位: 等云台稳下来再按快门
        ex.survey_dwell += dt
        if ex.survey_dwell < PTZ_SURVEY_DWELL_S:
            ex.detail = (f"survey view {ex.survey_idx + 1}/{total}: "
                         f"steady {ex.survey_dwell:.2f}s")
            return
        label = PTZ_SURVEY_LABELS[ex.survey_idx % len(PTZ_SURVEY_LABELS)]
        path = (self._shots.shoot(f"{ex.execution_id}-view{ex.survey_idx + 1}-{label}",
                                  ex.survey_dir) if self._shots else None)
        seen = self._target_in_frame(ex.target)
        ex.shots.append(path or "(no render)")
        ex.shot_seen.append(seen)
        ex.detail = (f"shot {ex.survey_idx + 1}/{total}: {label} "
                     f"target={'in frame' if seen else 'not in frame'}"
                     + (f" -> {os.path.basename(path)}" if path else " -> (no render)"))
        ex.survey_idx += 1
        ex.survey_dwell = 0.0

    def _target_in_frame(self, target: str) -> bool:
        """目标此刻是否真的落在 ptz_cam 的画面里: 量程 + 视场 + 视线遮挡。

        不是"知道坐标就算看见": 必须同时满足在量程内、视线没被实体截断、且相对
        光轴的偏角落在视场内 —— 三者都是按下快门那一刻的本地观察。
        """
        w = self.world
        try:
            tgt = w.body_xyz(target)
        except KeyError:
            return False
        eye = self.eye()
        d = tgt - eye
        if float(np.linalg.norm(d)) > PTZ_SURVEY_RANGE_M:
            return False
        if w.occluders.blocked(eye, tgt):
            return False
        pan_des = math.atan2(float(d[1]), float(d[0]))
        horiz = math.hypot(float(d[0]), float(d[1]))
        tilt_des = -math.atan2(float(d[2]), horiz)
        if abs(_wrap(pan_des - w.qpos(self.spec.pan_joint))) > PTZ_FOV_HALF_H_RAD:
            return False
        if abs(_wrap(tilt_des - w.qpos(self.spec.tilt_joint))) > PTZ_FOV_HALF_V_RAD:
            return False
        return True

    def _finish_survey(self, ex: Execution) -> None:
        """四张拍完后判定目标是否在机械臂抓取范围内, 并写入本地依据。"""
        pos = self.world.body_xyz(ex.target)
        r = self._arm_radius(pos)
        ik_ok = self._arm_ik(self._grasp_point(pos)) is not None
        seen = any(ex.shot_seen)
        shots = ", ".join(os.path.basename(p) for p in ex.shots) or "(none)"
        detail = (f"{len(ex.shots)} shots [{shots}]; {ex.target} at "
                  f"({pos[0]:.2f},{pos[1]:.2f}) r={r:.2f} m "
                  f"(limit {ARM_SWEEP_R_M:.2f}), ik={'ok' if ik_ok else 'unreachable'}, "
                  f"observed={seen}")
        if not seen:
            self._finish(ex, STATE_FAILED,
                         f"target not observed in any shot: {detail}",
                         BASIS_TARGET_NOT_OBSERVED)
            return
        if not ik_ok or r > ARM_SWEEP_R_M:
            self._finish(ex, STATE_FAILED,
                         f"target out of arm workspace: {detail}",
                         BASIS_OUT_OF_WORKSPACE)
            return
        self._finish(ex, STATE_COMPLETED,
                     f"target within arm workspace: {detail}",
                     BASIS_REACH_IN_WORKSPACE)

    def _gait(self, speed: float, dt: float) -> None:
        """四足 trot 步态: 仅作可视化, 不参与接触与运动学。"""
        if not self.spec.legs:
            return
        amp = 0.30 if speed > 0.01 else 0.0
        self._gait_phase += dt * (6.0 if speed > 0.01 else 0.0)
        ph = self._gait_phase
        offsets = {"fl": 0.0, "rr": 0.0, "fr": math.pi, "rl": math.pi}
        for name in self.spec.legs:
            leg = name[:2]
            base = offsets.get(leg, 0.0)
            swing = math.sin(ph + base)
            if name.endswith("upper"):
                self.world.set_ctrl(f"{name}_act", amp * swing)
            else:
                self.world.set_ctrl(f"{name}_act", -0.70 + 0.30 * swing)

    # ---------------- 机械臂 (Immediate How) ----------------

    @staticmethod
    def _arm_radius(point: np.ndarray) -> float:
        """点到基座的水平距离。"""
        return math.hypot(float(point[0]) - ARM_BASE_XY[0],
                          float(point[1]) - ARM_BASE_XY[1])

    @staticmethod
    def _arm_bearing(xy: np.ndarray) -> float:
        """点相对基座的方位角 (rad)。"""
        return math.atan2(float(xy[1]) - ARM_BASE_XY[1], float(xy[0]) - ARM_BASE_XY[0])

    @staticmethod
    def _arm_ik(target3: np.ndarray) -> tuple[float, float] | None:
        """二连杆解析逆解: 目标世界坐标 -> (肩, 肘) 关节角; 超出臂展返回 None。

        约定: 绕 +y 旋转 theta 时, 局部 +x 方向映射到 (cos theta, 0, -sin theta),
        因此关节角 = -仰角。肘关节为相对角 (gamma - pi <= 0, 即向内折)。
        """
        r = RoleRuntime._arm_radius(target3)
        dz = float(target3[2]) - ARM_SHOULDER_Z
        dist = math.hypot(r, dz)
        if dist > ARM_L1 + ARM_L2 - 1e-3 or dist < abs(ARM_L1 - ARM_L2) + 1e-3:
            return None
        cos_a = max(-1.0, min(1.0, (dist**2 + ARM_L1**2 - ARM_L2**2)
                              / (2.0 * ARM_L1 * dist)))
        cos_g = max(-1.0, min(1.0, (ARM_L1**2 + ARM_L2**2 - dist**2)
                              / (2.0 * ARM_L1 * ARM_L2)))
        alpha = math.acos(cos_a)
        gamma = math.acos(cos_g)
        # 二连杆有"肘朝上/肘朝下"两支解: 末端仰角 phi1 = atan2(dz, r) + alpha 取
        # **肘朝上** (肘在肩高附近, 小臂自上而下接近目标); 取 -alpha 则是肘朝下,
        # 大臂几乎垂直扎向地面 (肘关节落到 z~0), 小臂再反向折回 —— 实测末端会
        # 落到目标关于基座的镜像位置 (误差 >1 m), 且大臂穿地。故此处必须 +alpha。
        phi1 = math.atan2(dz, r) + alpha
        # 肘为相对角: 末端仰角 phi2 = phi1 - (pi - gamma), 而 psi = -theta,
        # 故 theta2 = -(phi2 - phi1) = pi - gamma (恒为正, 故肘关节范围取 [0, 2.9])
        return -phi1, math.pi - gamma

    @staticmethod
    def _grasp_point(obj_pos: np.ndarray) -> np.ndarray:
        """物体中心 -> 夹爪目标点: 抬到顶面之上, 使掌心压在顶面上而非埋进箱子。"""
        p = np.asarray(obj_pos, dtype=float).copy()
        p[2] += ARM_GRIP_LIFT_M
        return p

    def _arm_reach(self, target3: np.ndarray) -> bool:
        """把末端指向目标; 目标超出臂展时返回 False (由调用方写本地 FAILED)。"""
        self._arm_hold_q = None
        sol = self._arm_ik(target3)
        if sol is None:
            return False
        self.world.set_ctrl(f"{self.spec.arm_joints[0]}_act", sol[0])
        self.world.set_ctrl(f"{self.spec.arm_joints[1]}_act", sol[1])
        return True

    def _arm_fingers(self, value: float) -> None:
        """夹爪开合 (位置执行器: 0=张开, 0.06=闭合)。"""
        for finger in self.spec.fingers:
            self.world.set_ctrl(f"{finger}_act", value)

    def _arm_stow(self) -> None:
        """收臂姿态: 回转过程中保持, 避免末端扫过扇区里的实体。"""
        if not self.spec.arm_joints:
            return
        self._arm_hold_q = None
        self.world.set_ctrl(f"{self.spec.arm_joints[0]}_act", ARM_STOW[0])
        self.world.set_ctrl(f"{self.spec.arm_joints[1]}_act", ARM_STOW[1])

    def _arm_hold(self) -> None:
        """保持当前臂型: 位置执行器指令取**进入保持时锁定的关节角**, 回转速度归零。

        不能每个物理步都把指令改写成当前关节角 —— 那样指令会跟着重力下坠一起走,
        弹簧力恒为 0, 机械臂会以阻尼端速度持续下扎 (实测空闲 2 s 末端从 z=0 掉到
        -0.32 m)。锁定一次才能提供恢复力; 锁存在重新主动驱动时清空 (见 _arm_stow
        / _arm_reach), 使下一次保持重新捕获当时的臂型。
        """
        w = self.world
        w.set_ctrl("arm_yaw_act", 0.0)
        hold_joints = (*self.spec.arm_joints, *self.spec.fingers)
        if self._arm_hold_q is None:
            self._arm_hold_q = tuple(w.qpos(joint) for joint in hold_joints)
        for joint, value in zip(hold_joints, self._arm_hold_q):
            w.set_ctrl(f"{joint}_act", value)

    def _arm_release(self, ex: Execution) -> None:
        """终态前松手: 物体落在当前末端位置 (失败时也不能一直"叼着")。"""
        if not ex.held:
            return
        self._arm_carry(ex)
        self._arm_fingers(ARM_FINGER_OPEN)
        ex.held = False

    def _arm_carry(self, ex: Execution) -> None:
        """把被抓物体钉在夹爪末端 (运动学搬运), 松手后交还动力学。"""
        tip = self.world.site_xyz(self.spec.site)
        try:
            self.world.set_body_xyz(
                ex.target, tip + np.array([0.0, 0.0, -ARM_GRIP_LIFT_M]))
        except KeyError:
            ex.held = False

    @staticmethod
    def _outline(cx: float, cy: float, hx: float, hy: float) -> list[tuple[float, float]]:
        """实体水平外缘的采样点 (约 0.10 m 间距), 用于扇区相交判定。

        只用"中心 + 外接圆半径"会同时产生两种错误: 细长物体(墙体)被当成巨盘而
        过度阻挡, 边角已伸进扫掠圆的箱子又因中心在圈外被漏判 —— 后者会让机械臂
        持物撞上箱子 (运动学搬运会把箱子弹飞)。沿外缘采样两者都避免。
        """
        corners = [(cx - hx, cy - hy), (cx + hx, cy - hy),
                   (cx + hx, cy + hy), (cx - hx, cy + hy)]
        pts: list[tuple[float, float]] = []
        for i in range(4):
            x0, y0 = corners[i]
            x1, y1 = corners[(i + 1) % 4]
            steps = max(2, int(math.hypot(x1 - x0, y1 - y0) / 0.10))
            for s in range(steps):
                t = s / steps
                pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
        return pts

    @staticmethod
    def _in_sector(point: np.ndarray, hx: float, hy: float, now: float,
                   delta: float, sweep_r: float) -> bool:
        """实体是否与 `now -> now+delta` 的回转扇区相交。

        delta 可正可负、幅度可超过 pi (绕对侧的长弧), 因此**不能**把相对角 wrap
        到 (-pi, pi] —— 那样长弧扇区会被错误裁剪。统一折算到逆时针 [0, 2pi) 后:
        正向扫过 [0, delta], 反向扫过 [2pi+delta, 2pi]。
        """
        dx = float(point[0]) - ARM_BASE_XY[0]
        dy = float(point[1]) - ARM_BASE_XY[1]
        dist = math.hypot(dx, dy)
        rr = math.hypot(hx, hy)
        # 粗筛 1: 外接圆与环带 [内圈, 扫掠半径] 不相交
        if dist - rr > sweep_r or dist + rr < ARM_INNER_R_M:
            return False
        # 粗筛 2: 角度完全不相干 (仅当扇区不足半圈时成立)
        rel = _wrap(math.atan2(dy, dx) - now)
        half = math.pi if dist <= rr else math.atan2(rr, dist - rr)
        if abs(delta) < math.pi - half:
            lo, hi = (0.0, delta) if delta >= 0.0 else (delta, 0.0)
            if not (lo - half <= rel <= hi + half):
                return False
        for px, py in RoleRuntime._outline(float(point[0]), float(point[1]), hx, hy):
            ex, ey = px - ARM_BASE_XY[0], py - ARM_BASE_XY[1]
            d = math.hypot(ex, ey)
            if d > sweep_r or d < ARM_INNER_R_M:
                continue
            ccw = (math.atan2(ey, ex) - now) % (2.0 * math.pi)
            if delta >= 0.0:
                if ccw <= delta:
                    return True
            elif ccw >= 2.0 * math.pi + delta:
                return True
        return False

    def _footprint(self, name: str, is_geom: bool) -> tuple[np.ndarray, float, float] | None:
        """实体的水平足迹 (中心, 半宽 x, 半宽 y); 找不到返回 None。"""
        w = self.world
        try:
            geom = name if is_geom else f"{name}_geom"
            point = w.geom_xyz(geom)
            hx, hy = w.geom_footprint(geom)
        except KeyError:
            if is_geom:
                return None
            try:
                point = w.body_xyz(name)
            except KeyError:
                return None
            hx = hy = 0.22
        return point, hx, hy

    def _sweep_radius(self, ex: Execution) -> float:
        """当前回转真正扫过的半径。

        回转期间保持收臂姿态, 故为 ARM_ROTATE_R_M; 但持物回转时臂是伸展的,
        被抓物体随之划过大弧, 此时扫掠半径取"末端半径 + 物体余量"。
        """
        if not ex.held:
            return ARM_ROTATE_R_M
        tip = self.world.site_xyz(self.spec.site)
        return max(ARM_ROTATE_R_M, self._arm_radius(tip) + ARM_CARRY_MARGIN_M)

    def _sector_blocker(self, now: float, delta: float, sweep_r: float,
                        skip: str = "") -> str | None:
        """`now -> now+delta` 扇区内的第一个阻挡实体; 无则返回 None。

        这是"回转范围内有障碍物就转不动"的本地判定, 依据几何而非接触力。
        skip 为被搬运目标自身 —— 它此刻正被夹爪带着走, 不能算作阻挡自己。
        """
        for name in ARM_BLOCKER_GEOMS:
            foot = self._footprint(name, True)
            if foot is None:
                continue
            if self._in_sector(foot[0], foot[1], foot[2], now, delta, sweep_r):
                return f"geom:{name}"
        for name in ARM_BLOCKER_BODIES:
            if name == skip:
                continue
            foot = self._footprint(name, False)
            if foot is None:
                continue
            if self._in_sector(foot[0], foot[1], foot[2], now, delta, sweep_r):
                return f"body:{name}"
        return None

    def _arm_sweep_plan(self, target_bearing: float, sweep_r: float,
                        skip: str = "") -> tuple[float | None, str, str]:
        """规划本轮回转: (绝对目标角, 方向, 被挡说明)。

        短弧优先; 短弧被挡且回转关节无限位时改为反向绕行的长弧; 两条都挡才判
        定无法回转, 并在说明里分别列出两侧的阻挡实体 —— 这是"两侧都被挡"的
        本地证据, RoboGuide 侧只看到 FAILED + 该 terminal_basis。
        """
        now = self.world.qpos(self.spec.yaw_joint)
        short = _wrap(target_bearing - now)
        if abs(short) <= ARM_YAW_TOL_RAD:
            return now, "aligned", ""
        blocked: list[str] = []
        hit = self._sector_blocker(now, short, sweep_r, skip)
        if hit is None:
            return now + short, "short", ""
        blocked.append(f"short by {hit}")
        if not self.world.joint_unlimited(self.spec.yaw_joint):
            blocked.append("long:unavailable (yaw joint limited)")
            return None, "", "; ".join(blocked)
        delta = short - math.copysign(2.0 * math.pi, short)
        hit = self._sector_blocker(now, delta, sweep_r, skip)
        if hit is None:
            return now + delta, "long", ""
        blocked.append(f"long by {hit}")
        return None, "", "; ".join(blocked)

    def _probe_limit(self, now: float, direction: float, sweep_r: float,
                     skip: str) -> float:
        """朝 direction (+1/-1) 摆动时, 扇区里出现阻挡体之前还能转过的角度。

        只走到阻挡体在扇区中的**起始方位**之前 (再留 ARM_PROBE_MARGIN_RAD 余量),
        因此摆动是"试探"而不是"撞上去" —— 本场景的阻挡判定是几何 sweep, 不是
        接触动力学, 真的转过去只会穿模。
        """
        step = 0.02
        d = step
        while d <= ARM_PROBE_MAX_RAD:
            if self._sector_blocker(now, direction * d, sweep_r, skip=skip) is not None:
                return max(0.0, d - step - ARM_PROBE_MARGIN_RAD)
            d += step
        return ARM_PROBE_MAX_RAD

    def _probe_goals(self, target_bearing: float, sweep_r: float,
                     skip: str) -> list[float]:
        """两侧皆挡时的摆动序列 (绝对角): 短弧侧摆出去 -> 回中 -> 长弧侧 -> 回中。

        返回的序列由 align 阶段逐个消费, 消费完才写本地 FAILED, 于是"机械臂左右
        各摆一次、两侧都撞上东西"在录像里是可见的动作, 而不只是一条终态理由。
        """
        now = self.world.qpos(self.spec.yaw_joint)
        short = _wrap(target_bearing - now)
        sign = 1.0 if short >= 0.0 else -1.0
        goals: list[float] = []
        for direction in (sign, -sign):
            arc = self._probe_limit(now, direction, sweep_r, skip)
            if arc <= 0.05:
                continue
            goals.extend([now + direction * arc, now])
        return goals

    def _tick_relocate(self, ex: Execution, dt: float) -> None:
        """搬运一个物理步: 回转 -> 抓取 -> 回转 -> 放置; 每步都先查扇区。"""
        if ex.stage in ("align_object", "align_place"):
            aim = (self.world.body_xyz(ex.target)[:2] if ex.stage == "align_object"
                   else np.asarray(ex.goal, dtype=float))
            sweep_r = self._sweep_radius(ex)
            now = self.world.qpos(self.spec.yaw_joint)
            if ex.probe_dwell > 0.0:
                # 摆动到极限后的停顿: 让"这一侧被挡住"在画面上停留可见
                self.world.set_ctrl("arm_yaw_act", 0.0)
                ex.probe_dwell = max(0.0, ex.probe_dwell - dt)
                ex.detail = (f"probing {ex.stage}: rotation blocked ahead "
                             f"({ex.probe_blocked})")
                return
            if ex.yaw_goal is None:
                if ex.probe_goals:
                    ex.yaw_goal = ex.probe_goals.pop(0)
                    ex.yaw_way = "probe"
                elif ex.probe_blocked:
                    # 两侧都摆过了, 两侧都撞上东西 -> 本地判定无法回转
                    self._arm_release(ex)
                    self._arm_hold()
                    self._finish(ex, STATE_FAILED,
                                 f"rotation blocked in both directions "
                                 f"({ex.probe_blocked}) — probed both arcs",
                                 BASIS_SWEEP_BLOCKED_BOTH)
                    return
                else:
                    goal, way, blocked = self._arm_sweep_plan(
                        self._arm_bearing(aim), sweep_r, skip=ex.target)
                    if goal is None:
                        # 两条弧都挡: 不立刻判失败, 先左右各摆一次再判 —— 摆动
                        # 只走到阻挡体之前, 因此这是"试探"而非撞击。
                        ex.probe_blocked = blocked
                        ex.probe_goals = self._probe_goals(
                            self._arm_bearing(aim), sweep_r, skip=ex.target)
                        if not ex.probe_goals:
                            self._arm_release(ex)
                            self._arm_hold()
                            self._finish(ex, STATE_FAILED,
                                         f"rotation blocked in both directions "
                                         f"({blocked})",
                                         BASIS_SWEEP_BLOCKED_BOTH)
                            return
                        ex.yaw_goal = ex.probe_goals.pop(0)
                        ex.yaw_way = "probe"
                    else:
                        ex.yaw_goal, ex.yaw_way = goal, way
                        if way != "aligned":
                            ex.yaw_way_used = "+".join(
                                filter(None, [ex.yaw_way_used, way]))
            err = ex.yaw_goal - now
            if abs(err) > ARM_YAW_TOL_RAD:
                # 每步复查: 实体在回转途中才进入扇区 -> 立即终止 (松手 + 停机)。
                # 摆动探测跳过复查: 它的目标角本身就取在阻挡体之前, 复查必然命中,
                # 会把"两侧皆挡"错写成"单侧被挡"。
                if ex.yaw_way != "probe":
                    hit = self._sector_blocker(now, err, sweep_r, skip=ex.target)
                    if hit is not None:
                        self._arm_release(ex)
                        self._arm_hold()
                        self._finish(ex, STATE_FAILED,
                                     f"rotation blocked ({ex.yaw_way} arc) by {hit}",
                                     BASIS_SWEEP_BLOCKED)
                        return
                rate = ARM_PROBE_RATE if ex.yaw_way == "probe" else self.spec.max_yaw_rate
                self.world.set_ctrl("arm_yaw_act", max(-rate, min(rate, 2.5 * err)))
                if not ex.held:
                    self._arm_stow()
                ex.detail = (f"aligning ({ex.stage}, {ex.yaw_way} arc): "
                             f"{math.degrees(abs(err)):.1f} deg to go")
                return
            self.world.set_ctrl("arm_yaw_act", 0.0)
            if ex.yaw_way == "probe":
                # 摆到极限: 停一下再回摆 / 判失败
                ex.yaw_goal, ex.probe_dwell = None, ARM_PROBE_DWELL_S
                return
            ex.yaw_goal, ex.yaw_way = None, ""
            ex.stage = "grasp" if ex.stage == "align_object" else "place"
            return

        if ex.stage == "grasp":
            target3 = self._grasp_point(self.world.body_xyz(ex.target))
            if not self._arm_reach(target3):
                self._arm_release(ex)
                self._finish(ex, STATE_FAILED, f"{ex.target} left the workspace",
                             BASIS_OUT_OF_WORKSPACE)
                return
            self._arm_fingers(ARM_FINGER_OPEN)
            gap = float(np.linalg.norm(target3 - self.world.site_xyz(self.spec.site)))
            if gap < ARM_GRIP_TOL_M:
                self._arm_fingers(ARM_FINGER_CLOSED)
                ex.held = True
                if ex.complete_after == "place":
                    # 任务要求"搬到目的地": 继续走"回转 -> 放置", 终态由放置动作
                    # (夹爪真的松开且物体落地) 写入。被抓物体自身在扇区判定里被
                    # skip, 不会把自己当成阻挡体。
                    ex.stage = "align_place"
                    ex.detail = f"grasped {ex.target}, carrying to destination"
                    return
                # 拿起即成功: 任务要求是"抓起来", 不是"搬到位"。搬运到放置点
                # 这一段会把"目标物本身"变成回转阻挡体, 终态判定被它自己污染,
                # 因此成功条件就到这里为止 —— 放下与归位是之后的收尾动作。
                self._finish(ex, STATE_COMPLETED,
                             f"grasped {ex.target} "
                             f"(yaw: {ex.yaw_way_used or 'aligned'})", BASIS_GRASPED)
                self._settle, self._settle_stage, self._settle_t = ex, "down", 0.0
            ex.detail = f"reaching {ex.target}: {gap:.2f} m"
            return

        if ex.stage == "place":
            dest3 = np.array([ex.goal[0], ex.goal[1], ARM_PLACE_Z], dtype=float)
            if not self._arm_reach(dest3):
                self._arm_release(ex)
                self._finish(ex, STATE_FAILED, "destination left the workspace",
                             BASIS_OUT_OF_WORKSPACE)
                return
            gap = float(np.linalg.norm(dest3 - self.world.site_xyz(self.spec.site)))
            if gap < ARM_GRIP_TOL_M:
                self._arm_carry(ex)          # 先把物体放到位再松手
                self._arm_fingers(ARM_FINGER_OPEN)
                ex.held = False
                self._arm_stow()
                self._finish(ex, STATE_COMPLETED,
                             f"placed {ex.target} at destination "
                             f"(yaw: {ex.yaw_way_used or 'aligned'})", BASIS_PLACED)
                return
            ex.detail = f"placing: {gap:.2f} m"

    def _tick_settle(self, dt: float) -> None:
        """抓取成功后的收尾: 原地放下 -> 收臂 -> 转回初始朝向 (yaw=0)。

        终态上报之后物体还挂在末端, 这里把它平稳放回地面并把臂收回 yaw=0,
        使下一个 Mission 仍能演示一次完整回转 —— 否则下一次执行时臂已经对准
        目标, 画面上是"瞬间完成", 看不出短弧/长弧的差别。
        """
        ex = self._settle
        if ex is None:
            return
        w = self.world
        self._settle_t += dt
        if self._settle_stage == "down":
            tip = w.site_xyz(self.spec.site)
            dest3 = np.array([tip[0], tip[1], ARM_PLACE_Z], dtype=float)
            if not self._arm_reach(dest3):
                self._settle_stage, self._settle_t = "stow", 0.0
                return
            if float(np.linalg.norm(dest3 - w.site_xyz(self.spec.site))) < ARM_GRIP_TOL_M:
                self._arm_carry(ex)          # 先把物体落到地面高度再松手
                self._arm_fingers(ARM_FINGER_OPEN)
                ex.held = False
                self._settle_stage, self._settle_t = "stow", 0.0
            return
        if self._settle_stage == "stow":
            self._arm_stow()
            if self._settle_t > 0.8:
                self._settle_stage, self._settle_t = "home", 0.0
            return
        if self._settle_stage == "home":
            # 只朝"角度增大"方向回零: 从 180° 经 270°(-90°, 狗侧) 回到 360°(=0°),
            # 避开 +45° 一侧 (那里可能仍停着可推动障碍)。
            now = w.qpos(self.spec.yaw_joint)
            goal = math.ceil(now / (2.0 * math.pi)) * 2.0 * math.pi
            err = goal - now
            self._arm_stow()
            if abs(err) < 0.03 or self._settle_t > 12.0:
                w.set_ctrl("arm_yaw_act", 0.0)
                self._settle, self._settle_stage = None, ""
                return
            w.set_ctrl("arm_yaw_act", max(-self.spec.max_yaw_rate,
                                          min(self.spec.max_yaw_rate, 2.5 * err)))

    # ---------------- 对外状态 ----------------

    def status(self, execution_id: str) -> tuple[str, str] | None:
        """返回 (state, detail); 未知 handle 返回 None。"""
        with self.lock:
            ex = self.executions.get(execution_id)
            if ex is None:
                return None
            return ex.state, ex.detail

    def terminal_basis(self, execution_id: str) -> str:
        """该 execution 的本地终态依据; 未到终态或未知 handle 返回空串。

        依据只能由本地观察写入 (`_finish`), 控制面拿不到也推导不出 —— 它只能
        读到 FAILED + 依据字符串, 这正是"本地失败不污染控制面"的可观测面。
        """
        with self.lock:
            ex = self.executions.get(execution_id)
            return "" if ex is None else ex.terminal_basis

    def health(self) -> str:
        """本地运行时健康度: 物理世界可步进即视为在线。"""
        return "ONLINE" if math.isfinite(self.world.sim_time) else "OFFLINE"

    def health_detail(self) -> str:
        """health 明细: Local EAIOS 名、角色与当前仿真时刻, 便于观测推进速率。

        带自带感知的地面平台额外报出: 定位误差、自建地图的规模与覆盖率、以及
        在线标定出的里程计尺度 —— 这些是"它到底靠不靠谱"的唯一外部可观测量。
        """
        base = (f"{self.spec.runtime_name}/{self.spec.role} "
                f"sim_t={self.world.sim_time:.2f}s")
        if self.slam is not None:
            s = self.slam
            base += (f" | slam err={s.error():.2f}m cells={int(s.map.grid2d().sum())} "
                     f"cov={s.map.coverage() * 100:.0f}% "
                     f"scale={s.scale_factor:.3f} icp={s.icp_runs}")
        return base
