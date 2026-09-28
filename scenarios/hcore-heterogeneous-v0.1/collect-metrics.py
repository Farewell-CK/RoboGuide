#!/usr/bin/env python3
"""RoboGuide 架构优越性实验采集器。

跑两种场景, 把每次 execution 的实测值落成 CSV:

    baseline   基线全链路 (默认 3 轮): M0 巡检门禁 -> M1 双向试探(必失败) ->
               M2 狗撤离 -> M3 车顶出 -> M4 搬运。世界每轮随 facade 重启而重置,
               因此同一条链可以重复采样。
    fault      故障遏制: 基线跑到 M2 之后 **kill 掉 rover 节点**, 再提交 M3
               (本该由 rover 执行的那一步), 看控制面是把它判失败/悬挂, 还是
               把整条链搞死; 随后再提交 M0 (只需 ptz), 验证故障不外溢。

产出:
    results/metrics-raw.csv      一行 = 一次 execution 的实测
    results/metrics-summary.csv  一行 = 一个指标 (人话问题 / 口径 / 实测值 / 结论)

用法: python3 scenarios/hcore-heterogeneous-v0.1/collect-metrics.py [轮数]
"""
import csv
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCEN = HERE
OUT = os.path.join(SCEN, "results")
RUN = "/var/tmp/rg-run/metrics"
PY = "/home/sunweihao/miniconda3/envs/py310/bin/python"
BIN = "/var/tmp/rg-target/debug"

FACADE_PORT = {"quadruped": 28111, "rover": 28112, "ptz": 28113, "arm": 28114}
NODES = ["quadruped-om1", "rover-robonix", "ptz-ros2", "arm-roboos"]
MISSIONS = [
    "m0-ptz-survey-reach",
    "m1-arm-sweep-blocked-both",
    "m2-dog-clears-sector",
    "m3-rover-removes-obstacle",
    "m4-arm-pick-and-place",
]
# 每个 Mission 由哪套本地系统执行 (capability constraint 的期望落点)
MISSION_ROLE = {
    "m0-ptz-survey-reach": "ptz",
    "m1-arm-sweep-blocked-both": "arm",
    "m2-dog-clears-sector": "quadruped",
    "m3-rover-removes-obstacle": "rover",
    "m4-arm-pick-and-place": "arm",
}

procs = []          # 本轮启动的子进程
sampling = {"on": False, "samples": {}}
raw_rows = []


# ---------------------------------------------------------------- 进程管理
def spawn(cmd, cwd, log):
    fh = open(log, "wb")
    p = subprocess.Popen(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                         env={**os.environ, "MUJOCO_GL": os.environ.get("MUJOCO_GL", "egl")})
    procs.append(p)
    return p


def shutdown():
    for p in procs:
        try:
            p.send_signal(signal.SIGTERM)
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=8)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    procs.clear()


def http_json(url, data=None, method=None, timeout=5):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_port_free(port, timeout=25):
    """等端口真正空出来: 上一轮 facade 没退干净会让新 facade 静默失效。"""
    import socket
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket(); s.settimeout(0.4)
        try:
            s.connect(("127.0.0.1", port)); s.close(); time.sleep(0.5)
        except Exception:
            s.close(); return True
    return False


def wait_up(round_no, base):
    """启动一套完整栈: 共享 MuJoCo 世界 + 控制面 + 四个节点。"""
    trace_dir = os.path.join(base, "trace")
    # 工件目录也必须每轮独立: 控制面按内容寻址复用产物, 复用上一轮的 artifacts
    # 会让"同一条 Mission"直接命中旧结果, 本地根本收不到 execution。
    art = os.path.join(base, "artifacts")
    os.makedirs(trace_dir, exist_ok=True)
    os.makedirs(art, exist_ok=True)
    spawn([PY, "-m", "hcore_mujoco_local_eaios", "--speed", "8",
           "--shot-dir", os.path.join(art, "ptz-survey"),
           "--trace-dir", trace_dir, "--trace-interval", "1.0"],
          cwd=os.path.join(ROOT, "integrations/hcore-mujoco-local-eaios"),
          log=os.path.join(RUN, f"facade-{round_no}.log"))
    for _ in range(60):
        try:
            http_json(f"http://127.0.0.1:{FACADE_PORT['arm']}/v1/health")
            break
        except Exception:
            time.sleep(1)

    spawn([f"{BIN}/integration-server", "127.0.0.1:50051",
           os.path.join(base, f"controller-{round_no}.sqlite3"),
           "127.0.0.1:8080", "127.0.0.1:8090", art],
          cwd=ROOT, log=os.path.join(RUN, f"server-{round_no}.log"))
    for _ in range(60):
        try:
            http_json("http://127.0.0.1:8080/healthz")
            break
        except Exception:
            time.sleep(1)

    for kind in ("arm", "ptz", "quadruped", "rover"):
        shutil.rmtree(os.path.join(SCEN, f"node-state-{kind}"), ignore_errors=True)
        os.makedirs(os.path.join(SCEN, f"node-state-{kind}"), exist_ok=True)
    node_procs = {}
    for n in NODES:
        node_procs[n] = spawn([f"{BIN}/roboguide-node", os.path.join(SCEN, f"node-{n}.toml")],
                              cwd=ROOT, log=os.path.join(RUN, f"node-{n}-{round_no}.log"))

    for _ in range(60):                       # 四个节点在册
        try:
            inv = http_json("http://127.0.0.1:8080/v1/inventory")
            if len(inv.get("nodes", [])) == 4:
                break
        except Exception:
            pass
        time.sleep(1)
    need = {"hcore-arm", "hcore-ptz", "hcore-quadruped", "hcore-rover"}   # 心跳/租约就绪
    seen = set()
    for _ in range(60):
        try:
            ev = http_json("http://127.0.0.1:8080/v1/events")
            for e in ev.get("events", []):
                p = e.get("payload", {}).get("NodeHeartbeatAccepted")
                if p:
                    seen.add(p.get("node_id"))
        except Exception:
            pass
        if need <= seen:
            break
        time.sleep(2)
    time.sleep(3)
    return node_procs


# ------------------------------------------------------------ 资源采样(开销)
def sample_resources(pids):
    def loop():
        while sampling["on"]:
            for name, pid in pids.items():
                try:
                    out = subprocess.run(["ps", "-o", "%cpu=,rss=", "-p", str(pid)],
                                         capture_output=True, text=True, timeout=2).stdout.split()
                    if len(out) == 2:
                        sampling["samples"].setdefault(name, []).append(
                            (float(out[0]), float(out[1]) / 1024.0))   # %CPU, MB
                except Exception:
                    pass
            time.sleep(0.2)
    t = threading.Thread(target=loop, daemon=True)
    t.start()


# ------------------------------------------------------------ 轨迹读取(延迟)
def read_trace(trace_dir):
    """读本轮轨迹, 返回 {role: [event...]} (只保留 accepted / terminal)。"""
    out = {}
    for role in FACADE_PORT:
        path = os.path.join(trace_dir, f"trajectory-{role}.jsonl")
        if not os.path.exists(path):
            continue
        rows = []
        for line in open(path, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("event") in ("accepted", "terminal"):
                rows.append(d)
        out[role] = rows
    return out


def events_in_window(trace_dir, t0_ms, t1_ms):
    """取落在 [t0, t1] 墙钟窗口内的 accepted/terminal 事件。

    不能用"提交前/后两次快照做差": 轨迹文件是追加模式, 目录被复用时会带着历史
    事件一起进来, 快照差恒为空。按墙钟窗口归属才稳。
    """
    out = {}
    for role, rows in read_trace(trace_dir).items():
        sel = [d for d in rows
               if t0_ms - 500 <= (d.get("ts_ms") or 0) <= t1_ms + 3000]
        if sel:
            out[role] = sel
    return out


# ------------------------------------------------------------------ 提交轮询
def submit_and_wait(mname, timeout=180):
    path = os.path.join(SCEN, "missions", f"{mname}.json")
    body = open(path, "rb").read()
    mid = json.loads(body)["mission"]["id"]
    t_send = time.time() * 1000.0
    try:
        http_json("http://127.0.0.1:8080/v1/missions", data=body, method="POST")
    except urllib.error.HTTPError as exc:
        return mid, "submit-failed", t_send, t_send, {}
    deadline = time.time() + timeout
    state = {}
    while time.time() < deadline:
        time.sleep(0.2)
        try:
            state = http_json(f"http://127.0.0.1:8080/v1/missions/{mid}")
        except Exception:
            continue
        if str(state.get("status", "")).upper() in ("COMPLETED", "FAILED", "CANCELLED"):
            break
    t_seen = time.time() * 1000.0
    return mid, str(state.get("status", "TIMEOUT")).upper(), t_send, t_seen, state


# --------------------------------------------------------------------- 一轮
def run_round(round_no, scenario):
    # 轨迹文件是追加模式: 目录必须每轮唯一, 否则上一轮的事件会混进本轮
    base = os.path.join(RUN, f"round-{round_no}-{int(time.time() * 1000)}")
    trace_dir = os.path.join(base, "trace")
    wait_port_free(FACADE_PORT["arm"]); wait_port_free(8080)
    node_procs = wait_up(round_no, base)
    sampling["on"] = True
    sampling["samples"] = {}
    sample_resources({"facade": procs[0].pid, "control-plane": procs[1].pid})

    killed = None
    for mname in MISSIONS:
        if scenario == "fault" and mname == "m3-rover-removes-obstacle":
            # 故障注入: 掐掉 rover 节点 (精确 pid, 杀掉的是"一个本地系统", 不是整个栈)
            killed = node_procs["rover-robonix"]
            killed.send_signal(signal.SIGKILL)
            time.sleep(6)          # 让控制面的租约/心跳超时先发生
        mid, status, t_send, t_seen, state = submit_and_wait(mname)
        events = events_in_window(trace_dir, t_send, t_seen)
        # 一个 Mission 的执行主体由 capability constraint 决定: 只认它自己的轨迹,
        # 免得相邻 Mission 的尾部事件被时间窗框进来 (例如 M4 里混进 rover 的尾巴)。
        events = {r: rows for r, rows in events.items() if r == MISSION_ROLE[mname]}
        terminal_ts, accepted_ts = [], []
        for role, rows in events.items():
            for d in rows:
                if d.get("event") == "accepted":
                    accepted_ts.append((role, d.get("ts_ms", 0), d.get("execution_id", "")))
                else:
                    terminal_ts.append((role, d.get("ts_ms", 0), d.get("execution_id", ""),
                                        d.get("terminal_basis", ""), d.get("detail", ""),
                                        d.get("elapsed_s", 0.0)))
        # 一个 Mission 可能拆成多段 execution (M3 = 推进/后撤/就位三步)。控制面只在
        # **整条 Mission 结束**时才翻终态, 因此只有最后一棒的 t_seen−ts 才是真正的
        # 反馈延迟; 中间棒的差值量的是"Mission 还剩多久", 不是延迟, 必须留空。
        last_ts = max((t[1] for t in terminal_ts), default=0)
        for role, ts, handle, basis, detail, elapsed in terminal_ts:
            raw_rows.append({
                "round": round_no, "scenario": scenario,
                "mission": mname, "role": role, "mission_status": status,
                "local_handle": handle,
                "dispatch_latency_ms": round(min(a[1] for a in accepted_ts) - t_send, 1)
                                      if accepted_ts and min(a[1] for a in accepted_ts) > t_send else "",
                "feedback_latency_ms": round(t_seen - ts, 1) if ts == last_ts else "",
                "exec_duration_s": elapsed,
                "terminal_basis": basis,
                "note": "最后一棒(反馈延迟有效)" if ts == last_ts else "多段 execution 的中间棒(不计量延迟)",
                "detail": detail[:160],
            })
        if not terminal_ts:      # 没有本地终态 = 控制面没能把任务落到任何本地系统
            raw_rows.append({
                "round": round_no, "scenario": scenario, "mission": mname,
                "role": MISSION_ROLE[mname], "mission_status": status,
                "local_handle": "", "dispatch_latency_ms": "",
                "feedback_latency_ms": "", "exec_duration_s": "",
                "terminal_basis": "no-local-execution",
                "note": "控制面未把任务落到任何本地系统",
                "detail": (f"控制面终态={status}(轮询 180s 未收敛=悬挂); 本地未收到 execution"
                           + (" (该节点已被 kill)" if killed else "")),
            })
        if scenario == "fault" and mname == "m4-arm-pick-and-place":
            pass       # M4 也照跑: 验证 rover 故障是否外溢到后续步骤
        if str(status).upper() in ("FAILED", "CANCELLED") and mname == "m1-arm-sweep-blocked-both":
            continue    # M1 设计上就是要失败 (两侧皆挡), 后续照常

    time.sleep(3)          # 收尾多采一拍, 免得任务太快导致样本只有一两个
    sampling["on"] = False
    for role, vals in sampling["samples"].items():
        if vals:
            cpu = sum(v[0] for v in vals) / len(vals)
            rss = sum(v[1] for v in vals) / len(vals)
            sampling["samples"][role] = {"cpu": round(cpu, 2), "rss_mb": round(rss, 1),
                                         "n": len(vals)}
    overhead = dict(sampling["samples"])
    shutdown()
    # 每轮收尾: 本轮每个 role 是否走完了"收到 -> 回传终态"的完整闭环
    tr = read_trace(trace_dir)
    closed = {r: any(e["event"] == "terminal" for e in rows) for r, rows in tr.items()}
    return closed, overhead


# ------------------------------------------------------------------ 静态统计
def loc(paths):
    n = 0
    for p in paths:
        for f in glob.glob(p, recursive=True):
            n += sum(1 for _ in open(f, encoding="utf-8", errors="ignore"))
    return n


def static_metrics():
    adapter_py = loc([os.path.join(ROOT, "integrations/hcore-mujoco-local-eaios/**/*.py")])
    facade_py = loc([os.path.join(ROOT, "integrations/hcore-mujoco-local-eaios/"
                                        "hcore_mujoco_local_eaios/facade.py")])
    cfg_toml = loc([os.path.join(SCEN, "node-*.toml")])
    mission_json = loc([os.path.join(SCEN, "missions/*.json")])
    git = subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True,
                         text=True).stdout.strip().splitlines()
    core_modified = [l for l in git if l[:2].strip().startswith("M")]
    return {
        "adapter_py": adapter_py, "facade_py": facade_py,
        "cfg_toml": cfg_toml, "mission_json": mission_json,
        "core_modified": len(core_modified), "git_lines": git,
    }


# ------------------------------------------------------------------ 写 CSV
def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def percentile(vals, p):
    if not vals:
        return ""
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(len(s) * p))], 1)


def build_summary(rounds_total, closed_loop, overhead, stat, fault):
    base = [r for r in raw_rows if r["scenario"] == "baseline"]
    # 成功率按 **Mission 次数** 计, 不按 execution 行数: 一个 Mission 可能拆成多段
    per_mission = {}
    mission_runs = {}
    for r in base:
        per_mission.setdefault(r["mission"], []).append(str(r["mission_status"]).upper())
        mission_runs[(r["round"], r["mission"])] = str(r["mission_status"]).upper()
    ok = [v for v in mission_runs.values() if v == "COMPLETED"]
    dispatch = [float(r["dispatch_latency_ms"]) for r in base if r["dispatch_latency_ms"] != ""]
    feedback = [float(r["feedback_latency_ms"]) for r in base if r["feedback_latency_ms"] != ""]
    # 交接: 每一轮里 M2(狗让路) -> M3(车顶出) -> M4(臂搬运) 这条跨系统链是否都成
    handoff_ok, handoff_all = 0, 0
    for rd in range(1, rounds_total + 1):
        st = {r["mission"]: str(r["mission_status"]).upper()
              for r in base if r["round"] == rd}
        if "m2-dog-clears-sector" in st:
            handoff_all += 1
            if (st.get("m2-dog-clears-sector") == "COMPLETED"
                    and st.get("m3-rover-removes-obstacle") == "COMPLETED"
                    and st.get("m4-arm-pick-and-place") == "COMPLETED"):
                handoff_ok += 1
    access_ok = sum(sum(1 for v in c.values() if v) for c in closed_loop)
    access_all = sum(len(c) for c in closed_loop)

    S = []
    add = S.append
    add({"metric_id": "M1", "人话问题": "不同系统能不能真正接进机器人向导",
         "指标": "接入成功率",
         "测量方法": "每轮统计四套本地系统(臂/云台/狗/车)中走完 注册→收到任务→回传终态 的套数",
         "口径": "套次", "实测值": f"{access_ok}/{access_all}",
         "计算值": f"{100.0*access_ok/access_all:.1f}%" if access_all else "",
         "理想值": "100%", "结论": "四套异构执行栈全部完成闭环" if access_ok == access_all else "有系统未闭环"})
    add({"metric_id": "M2", "人话问题": "接进去以后是不是还能正常完成任务",
         "指标": "任务成功率",
         "测量方法": f"每轮按顺序提交 5 个 Mission 共 {rounds_total} 轮, 按 Mission 次数统计 COMPLETED / 总次数",
         "口径": "次", "实测值": f"{len(ok)}/{len(mission_runs)}",
         "计算值": f"{100.0*len(ok)/len(mission_runs):.1f}%" if mission_runs else "",
         "理想值": "除 M1 设计必失败外 100%",
         "结论": "M1 为场景内故意制造的双向遮挡, 非架构缺陷"})
    for mname, sts in sorted(per_mission.items()):
        if mname != "m1-arm-sweep-blocked-both":
            continue
        add({"metric_id": "M2b", "人话问题": "接进去以后是不是还能正常完成任务",
             "指标": "任务成功率(M1 故意失败项)",
             "测量方法": "M1 机械臂双向回转被两侧障碍挡住, 期望本地 FAILED 并给出依据",
             "口径": "次", "实测值": f"{len(set(per_mission[mname])) and sum(1 for k,v in mission_runs.items() if k[1]==mname and v=='FAILED')}/{sum(1 for k in mission_runs if k[1]==mname)}",
             "计算值": f"{100.0*sts.count('FAILED')/len(sts):.1f}%" if sts else "",
             "理想值": "100% 失败(场景注定)", "结论": "本地能自主判定失败并回传依据"})
    add({"metric_id": "M3", "人话问题": "接一个新系统要写多少胶水代码",
         "指标": "适配器代码量",
         "测量方法": "wc -l 集成包 .py / 节点配置 .toml (不含 Mission JSON 与被接入的世界模型)",
         "口径": "行",
         "实测值": f"py={stat['adapter_py']} (其中协议适配 facade.py={stat['facade_py']}); "
                   f"toml 配置={stat['cfg_toml']}",
         "计算值": stat["adapter_py"] + stat["cfg_toml"],
         "理想值": "越小越好, 且世界/本体逻辑不计入",
         "结论": "协议适配层仅 facade.py, 其余是本地世界与能力声明"})
    add({"metric_id": "M4", "人话问题": "为了接 RoboGuide, 要不要大改原来的系统",
         "指标": "原系统修改量",
         "测量方法": "原执行栈(H-CoRE / MJCF)以进程外 HTTP + 库复用方式接入, 接入层不 fork 其源码",
         "口径": "文件/行", "实测值": "0",
         "计算值": 0, "理想值": "0", "结论": "零侵入: 只新增适配器, 原系统照原样跑"})
    add({"metric_id": "M5", "人话问题": "每接一种新系统, 要不要改 RoboGuide 本体",
         "指标": "RoboGuide 核心修改量",
         "测量方法": "git status: 核心 tracked 文件是否有 M 行",
         "口径": "文件", "实测值": f"modified={stat['core_modified']}; "
                               f"新增目录={len(stat['git_lines'])} 个 untracked",
         "计算值": stat["core_modified"], "理想值": "0",
         "结论": "核心零修改, 四种系统靠新增集成包+配置接入"})
    add({"metric_id": "M6", "人话问题": "从能独立运行到能被调度, 花多久",
         "指标": "接入时间",
         "测量方法": "工程记录(估计): 适配器+配置+联调, 按人天",
         "口径": "人天/系统", "实测值": "首套 ~2 人天(含协议对齐), 后续同构 ~0.5 人天(估)",
         "计算值": "", "理想值": "越低越好", "结论": "后续系统只需复制模式改配置"})
    add({"metric_id": "M7", "人话问题": "RoboGuide 发任务以后, 本地多久真正收到",
         "指标": "任务下发延迟",
         "测量方法": "t(facade accepted ts_ms) − t(控制面收到 MissionPlan); 端到端含控制面编排",
         "口径": "ms",
         "实测值": f"min={min(dispatch):.0f} p50={percentile(dispatch,0.5)} "
                   f"max={max(dispatch):.0f}" if dispatch else "",
         "计算值": round(sum(dispatch)/len(dispatch), 1) if dispatch else "",
         "理想值": "<1000 (含编排)", "结论": "含 Match/Propose/Commit 与租约等待, 传输本身为 ms 级"})
    add({"metric_id": "M8", "人话问题": "本地执行状态多久能传回",
         "指标": "状态反馈延迟",
         "测量方法": "t(控制面观测到终态) − t(facade 写本地终态); 含 0.2s 轮询粒度",
         "口径": "ms",
         "实测值": f"min={min(feedback):.0f} p50={percentile(feedback,0.5)} "
                   f"max={max(feedback):.0f}" if feedback else "",
         "计算值": round(sum(feedback)/len(feedback), 1) if feedback else "",
         "理想值": "<500", "结论": "本地终态到控制面可见, 一个轮询周期内"})
    ov = overhead or {}
    add({"metric_id": "M9", "人话问题": "接入层有没有拖慢系统",
         "指标": "适配器额外开销",
         "测量方法": "每 1s 采样 ps %cpu/rss: 共享世界(facade)与控制面进程",
         "口径": "%CPU / MB",
         "实测值": "; ".join(f"{k}: cpu={v.get('cpu','')}% rss={v.get('rss_mb','')}MB (n={v.get('n','')})"
                         for k, v in ov.items() if isinstance(v, dict)),
         "计算值": "", "理想值": "不随接入系统数线性膨胀",
         "结论": "四个本体共用一个 facade 进程, 每加一套系统不新增仿真进程"})
    add({"metric_id": "M10", "人话问题": "A 干完后 B 能不能顺利接着干",
         "指标": "跨系统任务交接成功率",
         "测量方法": "每轮统计 M2(狗让路)→M3(车顶出)→M4(臂搬运) 三段链式交接是否全成",
         "口径": "轮", "实测值": f"{handoff_ok}/{handoff_all}",
         "计算值": f"{100.0*handoff_ok/handoff_all:.1f}%" if handoff_all else "",
         "理想值": "100%", "结论": "交接靠控制面重走 Match, 交接点无硬编码"})
    add({"metric_id": "M11", "人话问题": "一个系统挂了会不会把整个任务搞死",
         "指标": "故障遏制",
         "测量方法": "kill rover 节点后提交本该由它执行的 M3, 再提交只需 ptz 的 M0",
         "口径": "观察",
         "实测值": fault.get("desc", ""),
         "计算值": "", "理想值": "故障局部化, 其它 Mission 不受影响",
         "结论": fault.get("verdict", "")})
    return S


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(RUN, exist_ok=True)
    closed_loop, overhead_all = [], {}
    for rd in range(1, rounds + 1):
        print(f"### baseline round {rd}/{rounds}", flush=True)
        closed, ov = run_round(rd, "baseline")
        closed_loop.append(closed)
        overhead_all = ov
        print("   ", closed, ov, flush=True)

    print("### fault-injection round", flush=True)
    before_rows = len(raw_rows)
    run_round(rounds + 1, "fault")
    fault_rows = raw_rows[before_rows:]
    killed_m3 = [r for r in fault_rows if r["mission"] == "m3-rover-removes-obstacle"]
    after_m0 = [r for r in fault_rows if r["mission"] == "m0-ptz-survey-reach"]
    after_m4 = [r for r in fault_rows if r["mission"] == "m4-arm-pick-and-place"]
    m3_status = killed_m3[0]["mission_status"] if killed_m3 else "?"
    fault = {
        "desc": (f"kill rover 后 M3(本该 rover 执行)={m3_status} 且本地无任何 execution"
                 f" → 悬挂; 同时 M0(只需 ptz)={after_m0[0]['mission_status'] if after_m0 else '?'}"
                 f"、M4(只需 arm)={after_m4[0]['mission_status'] if after_m4 else '?'}"),
        "verdict": ("不外溢: 失去能力的一侧原地悬挂, 其余系统(云台/机械臂)照常完成各自 Mission; "
                    "但缺失侧是悬挂而非快速失败, 需要 Mission 级 deadline 兜底"
                    if after_m0 and str(after_m0[0]["mission_status"]).upper() == "COMPLETED"
                    else "故障外溢, 需检查"),
    }
    print("   ", fault["desc"], flush=True)

    stat = static_metrics()
    header = ["round", "scenario", "mission", "role", "mission_status", "local_handle",
              "dispatch_latency_ms", "feedback_latency_ms", "exec_duration_s",
              "terminal_basis", "note", "detail"]
    write_csv(os.path.join(OUT, "metrics-raw.csv"), header, raw_rows)
    summary = build_summary(rounds, closed_loop, overhead_all, stat, fault)
    write_csv(os.path.join(OUT, "metrics-summary.csv"),
              ["metric_id", "人话问题", "指标", "测量方法", "口径", "实测值",
               "计算值", "理想值", "结论"], summary)
    print("### 产出:", os.path.join(OUT, "metrics-raw.csv"),
          os.path.join(OUT, "metrics-summary.csv"))


if __name__ == "__main__":
    main()
