"""把 MuJoCo 世界暴露成 RoboGuide Node Service 可消费的本地 HTTP facade。

路由与语义完全对齐 `integrations/habitat-local-eaios` 的约定, 因此
`roboguide-node` 的 `node-config/v0.7` 声明式 workflow 无需任何特例:

    GET  /v1/health
    GET  /v1/capabilities/<operation>
    POST /v1/executions           -> {"execution_id": ..., "state": ..., "detail": ...}
    POST /v1/executions/status    -> {"state": ..., "detail": ...}
    POST /v1/executions/cancel    -> {"execution_id": ..., "state": ...}

响应必须是**扁平**的: 节点侧把整个响应体按步骤 id 记进 workflow context 的
`/steps/<step_id>`, 因此 node-config 里的 `local_handle.pointer =
/steps/dispatch/execution_id` 直指响应体顶层的 execution_id。若再包一层
`{"steps": {"dispatch": ...}}`, 该 pointer 就解析不到, 表现为
"mapping source `/steps/dispatch/execution_id` does not exist"。

铁律 (见 habitat bridge README): 终态只能由本地观察写入。Mission 完成不能伪造
本地 COMPLETED, 本地 COMPLETED 也不声称 Mission 成功。cancel 只持久化请求,
真正 CANCELLED 由物理循环观察到停机后才写入。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import mujoco

from .world import (
    DEFAULT_SHOT_DIR,
    ArenaWorld,
    ExecutionTracer,
    ROLE_SPECS,
    RoleRuntime,
    RoleSpec,
    Snapshotter,
    STATE_ACCEPTED,
)

DEFAULT_XML = os.path.join(
    os.environ.get(
        "HCORE_MJC_DIR", "/home/sdb/sunweihao/myproject/H-CoRE-sub/hcore_mjc_repro"
    ),
    "arena_roboguide.xml",
)


def _operation_name(raw: Any) -> str:
    """把 invocation 里的 operation 表示规范成 `namespace.name` 形式。

    兼容两种写法: 字符串 `"mobility.navigate@v1"` / `"mobility.navigate"`,
    以及结构化 `{"namespace": "mobility", "name": "navigate", "version": "v1"}`。
    """
    if isinstance(raw, str):
        return raw.split("@")[0]
    if isinstance(raw, dict):
        ns = raw.get("namespace", "")
        name = raw.get("name", "")
        return f"{ns}.{name}" if ns else name
    return ""


class FacadeHandler(BaseHTTPRequestHandler):
    """单个角色 facade 的 HTTP 处理器 (角色由类属性注入)。"""

    runtime: RoleRuntime = None  # type: ignore[assignment]
    spec: RoleSpec = None  # type: ignore[assignment]
    recorder: SceneRecorder | None = None   # 录像器 (未开录像时为 None)

    # ---------------- 基础工具 ----------------

    def log_message(self, fmt: str, *args: Any) -> None:
        """静默默认访问日志, 避免污染运行输出。"""
        return

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        """以 JSON 返回响应。"""
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        """解析 JSON 请求体, 失败返回空字典。"""
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # ---------------- 路由 ----------------

    def do_GET(self) -> None:
        """处理 health 与 capability readiness 查询。"""
        path = self.path.split("?")[0]
        if path == "/v1/health":
            self._send(200, {"state": self.runtime.health(),
                             "detail": self.runtime.health_detail()})
            return
        prefix = "/v1/capabilities/"
        if path.startswith(prefix):
            op = path[len(prefix):]
            supported = self._supports(op)
            self._send(200, {
                "state": "READY" if supported else "UNAVAILABLE",
                "detail": f"{self.spec.runtime_name} supports={supported} op={op}",
            })
            return
        self._send(404, {"state": "ERROR", "detail": f"no route: {path}"})

    def do_POST(self) -> None:
        """处理 execution 的 dispatch / status / cancel。"""
        path = self.path.split("?")[0]
        body = self._body()
        if path == "/v1/executions":
            self._dispatch(body)
        elif path == "/v1/executions/status":
            self._status(body)
        elif path == "/v1/executions/cancel":
            self._cancel(body)
        elif path == "/v1/record/start":
            self._record_start()
        else:
            self._send(404, {"state": "ERROR", "detail": f"no route: {path}"})

    def _supports(self, op: str) -> bool:
        """本本体是否支持该 canonical operation。"""
        if op.startswith("mobility.navigate"):
            return self.spec.slide is not None
        if op.startswith("observation.verify"):
            return bool(self.spec.capability)
        # 巡检: 只有带 pan/tilt 的云台能"转过四个方位各拍一张"
        if op.startswith("observation.survey"):
            return self.spec.pan_joint is not None
        if op.startswith("object.relocate"):
            return bool(self.spec.arm_joints)
        return False

    def _record_start(self) -> None:
        """显式开录 (配合 --record-defer 使用)。"""
        rec = self.recorder
        if rec is None:
            self._send(200, {"state": "NO_RECORDER", "detail": "facade 未开启录像"})
            return
        rec.start()
        self._send(200, {"state": "RECORDING", "detail": f"recording -> {rec.path}"})

    def _dispatch(self, body: dict[str, Any]) -> None:
        """登记 execution 并立即返回幂等 local handle。"""
        # 第一个任务到来 ---> 延迟模式下从此刻开始录像: 服务启动/注册/心跳等待
        # 期间画面完全静止, 录进去就是片头的空镜头。
        if self.recorder is not None:
            self.recorder.start()
        inv = body.get("invocation", body)
        op = _operation_name(inv.get("operation"))
        params = inv.get("parameters") or {}
        if isinstance(params, list):  # 结构化参数列表 -> 规约为标量字典
            params = {str(p.get("name")): p.get("value") for p in params
                      if isinstance(p, dict)}
        ex = self.runtime.submit(op, dict(params))
        self._send(200, {
            "execution_id": ex.execution_id,
            "state": ex.state,
            "detail": ex.detail,
        })

    def _status(self, body: dict[str, Any]) -> None:
        """查询 execution 的本地状态与明细。"""
        ex_id = body.get("execution_id") or (body.get("invocation") or {}).get(
            "execution_id")
        st = self.runtime.status(str(ex_id)) if ex_id else None
        if st is None:
            self._send(404, {"state": "UNKNOWN", "detail": f"unknown handle {ex_id}"})
            return
        state, detail = st
        self._send(200, {"state": state, "detail": detail,
                         "terminal_basis": self.runtime.terminal_basis(str(ex_id))})

    def _cancel(self, body: dict[str, Any]) -> None:
        """持久化取消请求; 不在此处伪造 CANCELLED。"""
        ex_id = body.get("execution_id") or (body.get("invocation") or {}).get(
            "execution_id")
        ok = self.runtime.cancel(str(ex_id)) if ex_id else False
        self._send(200, {
            "execution_id": str(ex_id),
            "state": "CANCEL_REQUESTED" if ok else "UNKNOWN_HANDLE",
        })


def _make_handler(runtime: RoleRuntime, spec: RoleSpec,
                  recorder: SceneRecorder | None = None) -> type[BaseHTTPRequestHandler]:
    """为给定角色生成一个绑定了 runtime 的 handler 子类。"""

    class BoundHandler(FacadeHandler):
        pass

    BoundHandler.runtime = runtime
    BoundHandler.spec = spec
    BoundHandler.recorder = recorder
    return BoundHandler


class SceneRecorder:
    """离屏录制物理世界画面 (无头环境可用, 不需要 X server 或 VNC)。

    场景里只有装在机器人身上的随体相机 (`rover_cam` / `dog_cam` / `ptz_cam`),
    没有全局俯视相机, 因此默认用 MuJoCo 的**自由相机**: 以 `model.stat.center`
    为注视点、`model.stat.extent` 为距离基准, 从而自动适配任何场景尺寸, 不必改
    MJCF。传 `--record-cam <name>` 可改用场景内已定义的相机 (随体视角)。

    节流按**仿真时间**而非墙钟, 因此视频时长等于仿真时长, 与 `--speed` 和渲染
    开销无关 —— 渲染慢只会拉长墙钟耗时, 不会让画面快进或丢帧。

    写入用 imageio 的流式 writer: 逐帧 append, 内存只保留一帧 (1280x720 的
    RGB 帧约 2.7 MB, 攒在内存里几分钟就会爆)。

    `defer=True` 时不立刻开录: 世界要先起服务、等注册/心跳/租约才能收到第一个
    任务, 这段时间画面完全静止 —— 直接从 bridge 启动就开始录会在片头留下几十秒
    空镜头。延迟模式下由**第一个本地 execution 被登记**时自动开录 (也可由
    `POST /v1/record/start` 显式开录)。

    `skip_idle=True` 时再进一步: **所有本体都没有动作**的那些仿真步不写帧。
    控制面在 Mission/Task 之间要重走 Match → Propose → Commit, 这段是秒级墙钟,
    世界一动不动 —— 跳过它们不会丢任何信息, 却能让视频里不出现静止片段。
    (机械臂探测两侧时的有意停顿属于执行中的状态, 不会被跳过。)
    """

    def __init__(self, world: ArenaWorld, path: str, fps: float = 30.0,
                 camera: str | None = None, width: int = 960, height: int = 540,
                 distance: float = 5.5, azimuth: float = 135.0,
                 elevation: float = -38.0, defer: bool = False,
                 skip_idle: bool = False,
                 idle_probe: Callable[[], bool] | None = None) -> None:
        try:
            import imageio.v2 as imageio
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise SystemExit(f"--record 需要 imageio: pip install imageio imageio-ffmpeg ({exc})")

        self.world = world
        self.path = path
        self.fps = float(fps)
        self._width = width
        self._height = height
        # Renderer **不能在这里创建**: EGL 上下文必须在使用它的线程里 makeCurrent,
        # 在主线程建、物理线程渲染会触发 eglMakeCurrent 的 EGL_BAD_ACCESS。
        # 故延迟到物理线程首次抓取时再建 (见 capture)。
        self._renderer = None
        self._disabled = False

        self._cam = mujoco.MjvCamera()
        # MjvOption 默认只渲染 geomgroup 0-2, 而任务目标 (crate1/markers/teddy)
        # 都在 group 3 —— 不打开的话画面里只剩它们的 2cm site 小球, 目标物看似
        # "只有指甲盖大"。这里把全部组打开 (dog_geom 靠 rgba alpha=0 隐身)。
        self._opt = mujoco.MjvOption()
        for grp in range(mujoco.mjNGROUP):
            self._opt.geomgroup[grp] = 1
        if camera:
            self._cam.type = mujoco.mjtCamera.mjCAMERA_FIXED
            self._cam.fixedcamid = mujoco.mj_name2id(
                world.model, mujoco.mjtObj.mjOBJ_CAMERA, camera)
            if self._cam.fixedcamid < 0:
                raise SystemExit(f"场景内没有名为 {camera!r} 的相机")
        else:
            # 自由相机取景用**绝对距离**而不是 stat.extent: 场景里的大地面会把
            # extent 撑到 11 m, 按 extent 取景会让围墙只占画面一小块。实测 5.5 m
            # 恰好框住围墙内全部区域 (4x4 m 竞技场)。
            self._cam.type = mujoco.mjtCamera.mjCAMERA_FREE
            self._cam.lookat[:] = (0.0, 0.0, 0.2)
            self._cam.distance = distance
            self._cam.azimuth = azimuth
            self._cam.elevation = elevation

        self._writer = imageio.get_writer(
            path, fps=self.fps, codec="libx264",
            output_params=["-crf", "20", "-pix_fmt", "yuv420p"])
        self._next_t = 0.0
        self.frames = 0
        self.skipped = 0
        self._armed = not defer     # 延迟模式下等 start() 才真正写帧
        self._skip_idle = skip_idle
        self._idle_probe = idle_probe

    def start(self) -> None:
        """开录 (延迟模式): 之后的第一帧立即抓取, 不留静止空镜头。"""
        self._armed = True

    def capture(self, sim_time: float) -> None:
        """到点时抓一帧; 渲染必须与 mj_step 互斥, 故持 world.lock。

        必须在**物理线程**里调用 (Renderer 在这里首次创建, 之后 EGL 上下文始终
        属于本线程)。
        """
        if self._disabled or not self._armed or sim_time < self._next_t:
            return
        if self._skip_idle and self._idle_probe is not None and self._idle_probe():
            # 全场无动作 (控制面正在编排下一棒): 跳过这一帧, 但**不推进**
            # _next_t —— 下一个有动作的仿真步会立刻被抓下来, 静止不会留下空隙。
            self.skipped += 1
            return
        if self._renderer is None:
            try:
                self._renderer = mujoco.Renderer(self.world.model,
                                                 height=self._height, width=self._width)
            except Exception as exc:  # pragma: no cover - 环境相关
                self._disabled = True
                print(f"[record] 离屏渲染不可用, 停止录制 "
                      f"(需 MUJOCO_GL=egl 且本机有 EGL 驱动): {exc}", file=sys.stderr)
                return
        with self.world.lock:
            self._renderer.update_scene(self.world.data, camera=self._cam,
                                        scene_option=self._opt)
            frame = self._renderer.render()
        self._writer.append_data(frame)
        self.frames += 1
        self._next_t = sim_time + 1.0 / self.fps

    def close(self) -> None:
        """收尾写出 moov atom —— 少了这一步 mp4 无法播放。"""
        try:
            self._writer.close()
        except Exception as exc:  # pragma: no cover - 收尾兜底
            print(f"[record] 关闭 writer 失败: {exc}", file=sys.stderr)
        else:
            extra = f", 跳过 {self.skipped} 个静止采样点" if self.skipped else ""
            print(f"[record] {self.frames} 帧 -> {self.path}{extra}", file=sys.stderr)


def _physics_loop(world: ArenaWorld, runtimes: list[RoleRuntime], speed: float,
                  stop: threading.Event, recorder: SceneRecorder | None = None) -> None:
    """物理主循环: 推进所有角色并配速, 直到收到停止信号。"""
    dt = float(world.model.opt.timestep)
    period = dt / max(speed, 0.1)
    try:
        while not stop.is_set():
            t0 = time.perf_counter()
            for rt in runtimes:
                rt.tick(dt)
            world.step(dt)
            if recorder is not None:
                recorder.capture(world.sim_time)
            remain = period - (time.perf_counter() - t0)
            if remain > 0:
                time.sleep(remain)
    finally:
        # 循环结束时收尾 (Ctrl-C / SIGTERM 都会走到这里), 否则 mp4 缺 moov atom。
        if recorder is not None:
            recorder.close()


def main(argv: list[str] | None = None) -> int:
    """启动一个共享世界 + 多个角色 facade。

    Args:
        argv: 命令行参数; 见 `--help`。

    Returns:
        进程退出码。
    """
    ap = argparse.ArgumentParser(description="H-CoRE MuJoCo Local EAIOS facade")
    ap.add_argument("--xml", default=DEFAULT_XML, help="MJCF 场景文件路径")
    ap.add_argument("--roles", nargs="+", default=["quadruped", "rover", "ptz", "arm"],
                    choices=sorted(ROLE_SPECS), help="要暴露的角色")
    ap.add_argument("--ports", nargs="+", type=int, default=[28111, 28112, 28113, 28114],
                    help="与 --roles 一一对应的监听端口")
    ap.add_argument("--speed", type=float, default=4.0,
                    help="仿真倍速 (1.0 = 实时)")
    ap.add_argument("--record", metavar="PATH", default=None,
                    help="离屏录制视频到 PATH (.mp4); 不传则不录制")
    ap.add_argument("--record-fps", type=float, default=15.0,
                    help="录制帧率, 按仿真时间节流 (默认 15)")
    ap.add_argument("--record-cam", default=None,
                    help="用场景内的相机名 (rover_cam/dog_cam/ptz_cam); "
                         "默认用全局自由相机俯视整个竞技场")
    ap.add_argument("--record-distance", type=float, default=5.5,
                    help="自由相机距离, 单位米 (默认 5.5, 恰好框住围墙内区域)")
    ap.add_argument("--record-width", type=int, default=960, help="录制宽度")
    ap.add_argument("--record-height", type=int, default=540, help="录制高度")
    ap.add_argument("--record-defer", action="store_true",
                    help="延迟录像: 服务启动/注册/心跳等待期间画面静止, 不录; "
                         "由第一个 execution 到达 (或 POST /v1/record/start) 开始录")
    ap.add_argument("--record-skip-idle", action="store_true",
                    help="跳过无动作的仿真步 (控制面在 Mission/Task 之间重编排的那几秒), "
                         "视频里不出现静止片段")
    ap.add_argument("--shot-dir", default=None,
                    help=f"PTZ 巡检照片落盘目录 (默认 {DEFAULT_SHOT_DIR}, "
                         f"也可由 execution 参数 out_dir 覆盖)")
    ap.add_argument("--trace-dir", default=None,
                    help="把每个 execution 的逐条运行轨迹落成 JSONL: "
                         "<DIR>/trajectory-<role>.jsonl (不传则不记录)")
    ap.add_argument("--trace-interval", type=float, default=1.0,
                    help="轨迹采样间隔, 按**仿真时间**计 (默认 1.0 s, "
                         "与 --speed 无关: 倍速不会让轨迹变稀)")
    ap.add_argument("--runtime-name", default=None,
                    help="把所有角色上报的 Local EAIOS 名字**统一**成一个值 "
                         "(同构对照实验用: 四个槽位都报 robonix-os, 只留本体与能力差异); "
                         "不传则各角色沿用自己的 runtime_name")
    ap.add_argument("--slam", choices=("on", "off"), default="on",
                    help="地面平台是否用自带的深度相机 + 雷达建三维地图并自己定位 "
                         "(on: 导航闭环与感知只用估计量; off: 退回读世界真值)")
    ap.add_argument("--slam-seed", type=int, default=0,
                    help="传感器噪声/开机定位误差的随机种子 (对照实验用)")
    ap.add_argument("--slam-dump", metavar="DIR", default=None,
                    help="退出时把每个本体自建的三维地图导成 PLY 点云到该目录")
    args = ap.parse_args(argv)

    if len(args.ports) != len(args.roles):
        print("--ports 与 --roles 数量必须一致", file=sys.stderr)
        return 2

    world = ArenaWorld(args.xml)
    # 拍照设施在所有角色间共享 (Renderer 惰性创建, 只有 PTZ 巡检会用到它);
    # 目录在这里就建好, 免得第一次按快门时才失败。
    shots = Snapshotter(world, args.shot_dir or DEFAULT_SHOT_DIR)
    os.makedirs(shots.out_dir, exist_ok=True)
    # 轨迹日志: 每个角色一个文件, 逐条记录 accepted / sample / terminal。节点
    # stdout 是空的、控制面只有终态, 过程明细只有这里留得下来。
    tracers: dict[str, ExecutionTracer | None] = {r: None for r in args.roles}
    if args.trace_dir:
        os.makedirs(args.trace_dir, exist_ok=True)
        for r in args.roles:
            tracers[r] = ExecutionTracer(
                os.path.join(args.trace_dir, f"trajectory-{r}.jsonl"), r,
                args.trace_interval)
    # 同构对照开关: 只改"上报的 OS 名字", 本体/运动学/能力集一个不动 —— 用来验证
    # 控制面的调度不依赖各节点 OS 名字不同。
    specs = dict(ROLE_SPECS)
    if args.runtime_name:
        for r, s in specs.items():
            specs[r] = dataclasses.replace(s, runtime_name=args.runtime_name)
    runtimes = [RoleRuntime(world, specs[r], shots, tracers[r],
                            slam=(args.slam == "on"), slam_seed=args.slam_seed)
                for r in args.roles]

    recorder = None
    if args.record:
        # 空闲探针: 所有本体都没有在跑任务 —— 此时控制面多半正在为下一棒重走
        # Match/Propose/Commit, 画面静止, 录下来没有信息量。
        recorder = SceneRecorder(world, args.record, fps=args.record_fps,
                                 camera=args.record_cam, width=args.record_width,
                                 height=args.record_height, distance=args.record_distance,
                                 defer=args.record_defer,
                                 skip_idle=args.record_skip_idle,
                                 idle_probe=lambda: not any(rt.busy() for rt in runtimes))

    stop = threading.Event()
    # SIGTERM 必须优雅退出: 脚本用 kill 收尾, 若进程直接死掉, mp4 会缺 moov atom
    # 而无法播放。这里只置事件, 由主循环退出后统一收尾。
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    phys = threading.Thread(target=_physics_loop,
                            args=(world, runtimes, args.speed, stop, recorder),
                            daemon=True)
    phys.start()

    servers = []
    for role, port in zip(args.roles, args.ports):
        rt = runtimes[args.roles.index(role)]
        srv = ThreadingHTTPServer(("127.0.0.1", port),
                                  _make_handler(rt, specs[role], recorder))
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"[facade] {role:9s} runtime={specs[role].runtime_name:20s} "
              f"-> http://127.0.0.1:{port}")

    if recorder:
        print(f"[record] 录制中 -> {args.record} "
              f"({args.record_width}x{args.record_height} @{args.record_fps:g}fps)")
    if args.trace_dir:
        print(f"[trace ] 轨迹日志 @每{args.trace_interval:g}s 仿真时间 "
              f"-> {os.path.join(args.trace_dir, 'trajectory-<role>.jsonl')}")
    print(f"[world ] {args.xml}  speed={args.speed}x  (Ctrl-C 退出)")
    try:
        while not stop.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop.set()
    finally:
        # 先等物理线程收尾 (它负责 close writer), 再关 HTTP。
        phys.join(timeout=180.0)
        # 本体自建的三维地图只存在于内存里, 进程一退就没了 —— 退出前落盘。
        if args.slam_dump:
            for rt in runtimes:
                if rt.slam is not None:
                    out = os.path.join(args.slam_dump, f"map-{rt.spec.role}.ply")
                    n = rt.slam.dump_ply(out)
                    print(f"[slam  ] {rt.spec.role}: {n} 个占据体素 -> {out}")
        for srv in servers:
            srv.shutdown()
        for tr in tracers.values():
            if tr is not None:
                tr.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
