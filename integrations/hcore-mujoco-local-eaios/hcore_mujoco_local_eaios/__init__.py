"""hcore-mujoco-local-eaios: 把 H-CoRE MuJoCo 竞技场接成 RoboGuide 的 Local EAIOS。

该包是 deployment-owned bridge, 不属于 RoboGuide Core。它复用 H-CoRE MuJoCo
复现场景的几何与导航设施, 只为三个异构本体提供 Local How; 不拥有 Mission、
Execution Group、State Catalog 或 Node Protocol 生命周期。
"""

__all__ = ["world", "facade"]
