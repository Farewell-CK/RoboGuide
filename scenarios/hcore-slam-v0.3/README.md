# 场景：全 Robonix OS + 本体自带三维建图与定位 — `hcore-slam-v0.3`

在 `hcore-homogeneous-v0.2`（四个节点全部声明 `robonix-os`）的基础上，只加一件事：
**地面平台不再"知道"自己在哪** —— 它们靠自己装的深度相机和雷达建三维地图、自己在
地图里定位，导航闭环与"看见了没有"全部只用估计量。

## 为什么要做这一版

前两版里，地面平台的位置是直接从仿真世界读出来的真值。这留了一个很大的反驳：

> 导航闭环用的是上帝视角的坐标，那这个"自主导航"有多少是真的？

这一版把真值从闭环里拿掉：

| | v0.1 / v0.2 | v0.3 |
|---|---|---|
| 导航反馈量 | `world.body_xyz()` 真值 | **自己估计的位姿**（里程计 + 雷达 ICP + 地图匹配） |
| 路径规划 | 仿真栅格 | **自己建的三维地图**的二维投影（覆盖不到才回退） |
| 到点判定 | 真值距离 < 0.15 m | 估计位姿距离 < 0.28 m |
| "看见目标了吗" | 几何真值算距离/遮挡 | **朝目标打一条雷达射线**，看回波是否吻合 |
| 感知来源 | 无 | 狗：深度相机 + 雷达；车：深度相机 + 雷达（都挂在 MJCF 的真实安装位） |

真值只在三处出现：产生传感器回波、开机时的初始位姿（含一次性误差）、事后评估误差。

## 本体装了什么

| 本体 | 传感器 | 安装位（本体坐标系） | 规格 |
|---|---|---|---|
| 四足狗 `quadruped` | 雷达 + 深度相机 | 雷达 `(0, 0, 0.20)`，相机 `(0.62, 0, 0.20)` | 360°×3 层 / 8 m / 2°；相机 640×480 / 70° / 5 m |
| 地面车 `rover` | 雷达 + 深度相机 | 雷达 `(0, 0, 0.12)`，相机 `(0.20, 0, 0.05)` | 同上 |

雷达与相机都是**真射线求交**打在 MJCF 场景上（`mj_ray`），带高斯测距噪声（σ = 1 cm + 1% 量程）
与随机丢点（2%）；相机带 0.5% 深度噪声与 1% 像素缺失。里程计出厂未标定（尺度误差 6% σ），
由扫描匹配在线标定回来。

## 怎么跑

```bash
cd RoboGuide
export RUSTUP_HOME=/var/tmp/rg-rustup CARGO_HOME=/var/tmp/rg-cargo CARGO_TARGET_DIR=/var/tmp/rg-target
. $CARGO_HOME/env

# Step 1：起全链路（Ctrl-C 结束）
bash scenarios/hcore-slam-v0.3/run-step1-bringup.sh

# Step 2：五个 MissionPlan v0.7（约 3 分钟）
bash scenarios/hcore-slam-v0.3/run-step2-mission.sh
```

可调项（环境变量）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `RG_SLAM` | `on` | `off` = 关闭自带感知，退回读真值（对照用） |
| `RG_SLAM_SEED` | `7` | 传感器噪声 / 开机定位误差的种子 |
| `RG_SPEED` | `2` | 倍速。**带 SLAM 时算力上限约 2.8x 实时**，设更高只会拉长墙钟 |
| `RG_RUNTIME_NAME` | `robonix-os` | 四个槽位统一上报的 OS 名 |

跑完的工件在 `/var/tmp/rg-run/hcore-slam-2/`：

```
trajectory-{quadruped,rover,ptz,arm}.jsonl   逐条运行轨迹（含每个采样点的定位误差）
artifacts/map-quadruped.ply                  狗自己建的三维地图（PLY 点云）
artifacts/map-rover.ply                      车自己建的三维地图
artifacts/ptz-survey/*.png                   M0 巡检四方位照片
```

## 实测结果（seed=7）

| 本体 | 定位误差 mean / max | 自建地图 | 路径规划来源 |
|---|---|---|---|
| 四足狗 | **0.048 / 0.063 m** | 5266 个占据体素，覆盖 27% | self-map |
| 地面车 | **0.047 / 0.071 m** | 7986 个占据体素，覆盖 32% | self-map（3 次导航全部） |

五个 Mission 的终态与异构版/同构版完全一致（M0 Completed、M1 Failed 门禁、M2/M3/M4 Completed），
说明换成本体自己定位之后，**跨 OS 恢复链照样成立**。
