"""Publish original navigation and manipulation histories without rerunning either arm."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from roboguide_eval.e1_fairness import PairManifest, PopulationManifest
from roboguide_eval.e1_publication import _counts, seal_package, validate_package
from roboguide_eval.e1_staged_import import _label, _run, provider_projection
from roboguide_eval.publication_io import (
    ArchiveReader,
    boolean,
    code_identity,
    encode,
    number,
    object_value,
    publication_metadata,
    sha256,
    write_new,
)


def _job(row: dict[str, Any], family: str, source: str, digest: str) -> dict[str, Any]:
    """Retain every frozen selector row before attaching any observed execution."""
    return {
        "job_id": row["pair_id"],
        "family": family,
        "pair_id": row["pair_id"],
        "episode_id": str(row.get("expected_episode_id", row.get("episode_id"))),
        "scene_id": row.get("expected_scene_id", row.get("scene_id")),
        "seed": str(row["seed"]),
        "population_digest": digest,
        "state": "unknown",
        "roboguide_run": "",
        "emos_run": "",
        "gap_code": "original_pair_outcome_inaccessible",
        "source_ref": source,
    }


def _navigation_run(
    reader: ArchiveReader, job: dict[str, Any], original: dict[str, Any], source: str
) -> dict[str, Any]:
    """Translate the original navigation layout, keeping model and process evidence separate."""
    directory = Path(original["directory"])
    manifest, manifest_ref = reader.document(directory / "RUN_MANIFEST.json")
    selector = manifest["pair"]
    if any(str(selector[k]) != str(job[k]) for k in ("pair_id", "episode_id", "scene_id", "seed")):
        raise ValueError("navigation run detached from frozen workload")
    outcome, outcome_ref = reader.document(directory / "OUTCOME.json")
    if outcome.get("official_pddl_success") != original.get("official_pddl_success"):
        raise ValueError("navigation report and original arm outcome disagree")
    raw = {
        **outcome,
        "arm": "emos" if original["arm"] == "native" else original["arm"],
        "process": {"directory": str(directory), "elapsed_seconds": original["elapsed_seconds"]},
        "evidence_directory": str(directory),
        "system_outcome": outcome.get("mission_status") or "UNKNOWN",
        "physical_episode_executed": True if number(outcome.get("simulator_steps")) else None,
    }
    projected = _run(
        reader,
        directory,
        {
            "job_id": job["job_id"],
            "family": job["family"],
            "expected_episode_id": job["episode_id"],
        },
        raw,
        outcome_ref,
        "primary",
        False,
        None,
    )
    projected["code_sha"] = code_identity(manifest.get("code_sha"))
    projected["code_identity_basis"] = "original RUN_MANIFEST.code_sha; not native vendor SHA"
    files = ("accounting/chat.jsonl",) + (
        ("accounting/mi.jsonl",) if raw["arm"] == "roboguide" else ()
    )
    provider, refs = provider_projection(reader, directory, files)
    provider["calls_reported"] = provider["calls_archived"]
    provider["call_count_basis"] = "archived accounting records; no invented missing calls"
    provider["model_identity_verified_as_recorded"] = None
    provider["accounting_complete_as_recorded"] = None
    projected["provider"] = provider
    projected["evidence_refs"] += [source, manifest_ref, *refs]
    if original.get("terminal_evidence"):
        _, ref = reader.read(Path(original["terminal_evidence"]), optional=True)
        projected["evidence_refs"].append(ref)
    projected["gaps"] = sorted(
        set(projected["gaps"])
        - {
            "original_launch_code_identity_unavailable",
            "provider_accounting_count_unavailable_or_mismatch",
        }
        | {
            "historical_navigation_pair_qualification_unavailable",
            "provider_accounting_closure_not_reverified",
        }
    )
    if projected["code_sha"] is None:
        projected["gaps"].append("original_run_code_identity_unavailable")
    if value := _label(outcome.get("controlled_stop_reason")):
        projected["failure_codes"].append(value)
    projected["failure_codes"] = sorted(set(projected["failure_codes"]))
    return projected


def _navigation(
    reader: ArchiveReader, cfg: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Bind the complete original navigation plan to original and continuation executions."""
    root = Path(cfg["navigation_root"])
    original_root = Path(cfg["navigation_original_root"])
    plan, plan_ref = reader.document(original_root / "PRE_REGISTRATION.json")
    continuation, continuation_ref = reader.document(root / "PRE_REGISTRATION.json")
    report, report_ref = reader.document(root / "BATCH_REPORT.json")
    if report.get("state") != "completed" or report.get("active"):
        raise ValueError("navigation archive has no completed original supervisor receipt")
    planned = {r["pair_id"]: r for r in plan["pairs"]}
    resumed = {r["pair_id"]: r for r in continuation["pairs"]}
    if len(planned) != len(plan["pairs"]) or not set(resumed) <= set(planned):
        raise ValueError("navigation continuation is not a subset of the original plan")
    seen: set[tuple[str, str]] = set()
    runs = []
    jobs = []
    pairs = []
    frozen_workloads = []
    for position, row in enumerate(plan["pairs"]):
        job = _job(row, "navigation", plan_ref + f"#/pairs/{position}", "")
        selected_row = resumed.get(row["pair_id"], row)
        if selected_row.get("input"):
            frozen, frozen_ref = reader.document(Path(selected_row["input"]), optional=True)
            if frozen:
                if any(str(frozen[k]) != str(job[k]) for k in ("episode_id", "scene_id", "seed")):
                    raise ValueError("navigation frozen input detached from original workload")
                frozen_workloads.append(
                    {
                        "pair_id": job["pair_id"],
                        "source_ref": frozen_ref,
                        "dataset_revision": frozen.get("dataset_revision"),
                        "dataset_sha256": frozen.get("dataset_sha256"),
                        "goal_sha256": sha256(encode(frozen.get("goal_predicates"))),
                        "instruction_sha256": sha256(encode(frozen.get("instruction"))),
                    }
                )
        raw = [a for a in report["arms"] if a["pair_id"] == row["pair_id"]]
        for arm in raw:
            identity = (arm["pair_id"], arm["arm"])
            if identity in seen:
                raise ValueError("duplicate historical navigation arm")
            seen.add(identity)
            record = _navigation_run(reader, job, arm, report_ref)
            expected_code = (
                continuation["code_sha"] if row["pair_id"] in resumed else plan["code_sha"]
            )
            if record["code_sha"] != expected_code:
                raise ValueError("navigation run code disagrees with original continuation")
            job[record["arm"] + "_run"] = record["run_key"]
            runs.append(record)
        if job["roboguide_run"] and job["emos_run"]:
            job.update(state="ended", gap_code="")
            pairs.append(
                {
                    "job_id": job["job_id"],
                    "family": "navigation",
                    "runs": [job["emos_run"], job["roboguide_run"]],
                    "recorded_comparison_eligible": None,
                    "comparability": None,
                    "reset_status": None,
                    "source_refs": [report_ref],
                    "strict_organization_only_claim": False,
                }
            )
        jobs.append(job)
    if len(seen) != len(report["arms"]):
        raise ValueError("navigation report includes unplanned executions")
    return (
        jobs,
        runs,
        pairs,
        {
            "source_refs": [plan_ref, continuation_ref, report_ref],
            "populations": {},
            "original_model_declaration": report.get("model"),
            "selection": (
                "complete original navigation plan; continuation partition preserved, "
                "not score-selected"
            ),
            "frozen_workloads": frozen_workloads,
            "original_limits": continuation.get("limits"),
            "legacy_assessments": [],
            "original_reporter_eligible_pairs": None,
        },
    )


def _height(
    reader: ArchiveReader, cfg: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Retain the complete manipulation selector, migration provenance and separate old rechecks."""
    root = Path(cfg["height_root"])
    manifest, manifest_ref = reader.document(root / "MANIFEST.json")
    plan, plan_ref = reader.document(root / "frozen/population.json")
    population = PopulationManifest.from_json(plan)
    history, history_ref = reader.document(root / "frozen/MIGRATION_HISTORY.json")
    status, status_ref = reader.document(root / "CONTINUATION_STATUS.json")
    final, final_ref = reader.document(root / "FINAL_BATCH_SUMMARY.json")
    reporter, reporter_ref = reader.read(root / "continue_and_report.py")
    if status.get("state") != "completed" or final["registered_pairs"] != len(
        plan["selector"]["rows"]
    ):
        raise ValueError("manipulation archive has no complete original closure")
    if population.digest != manifest["population_digest"]:
        raise ValueError("historical population digest disagrees with original declaration")
    retained = object_value(history["retained_pairs"])
    if not set(retained) <= {r["pair_id"] for r in plan["selector"]["rows"]}:
        raise ValueError("retained migration includes an unplanned workload")
    if (
        reporter is None
        or b'outcome.get("fatal_failure") is False' not in reporter
        or b'identifier != "pair-0000"' not in reporter
    ):
        raise ValueError("unsupported original historical reporter policy")
    jobs, runs, pairs = [], [], []
    for index, row in enumerate(plan["selector"]["rows"]):
        job = _job(row, "height_man", plan_ref + f"#/selector/rows/{index}", population.digest)
        provenance = retained.get(row["pair_id"])
        source_root = Path(provenance["directory"]) if provenance else root
        directory = source_root / "pairs" / row["pair_id"]
        outcome, outcome_ref = reader.document(directory / "pair-outcome.json", optional=True)
        if not outcome:
            jobs.append(job)
            continue
        for index_arm, arm in enumerate(outcome["arms"]):
            record = _run(
                reader,
                root,
                {
                    "job_id": job["job_id"],
                    "family": "height_man",
                    "expected_episode_id": job["episode_id"],
                },
                arm,
                outcome_ref + f"#/arms/{index_arm}",
                "primary",
                bool(provenance),
                None,
            )
            expected_code = provenance["code_sha"] if provenance else manifest["code_sha"]
            if record["code_sha"] != expected_code:
                raise ValueError("manipulation run code disagrees with original migration")
            record["evidence_refs"].append(history_ref if provenance else manifest_ref)
            runs.append(record)
            job[record["arm"] + "_run"] = record["run_key"]
        if job["emos_run"] and job["roboguide_run"]:
            job.update(state="ended", gap_code="")
            pair, pair_ref = reader.document(directory / "pair-manifest.json")
            parsed = PairManifest.from_json(pair)
            if (
                parsed.pair_id != job["pair_id"]
                or parsed.population_manifest_digest != population.digest
            ):
                raise ValueError("historical pair detached from original population")
            pairs.append(
                {
                    "job_id": job["job_id"],
                    "family": "height_man",
                    "runs": [job["emos_run"], job["roboguide_run"]],
                    "recorded_comparison_eligible": boolean(outcome.get("comparison_eligible")),
                    "comparability": pair["comparability"],
                    "reset_status": (outcome.get("reset_comparison") or {}).get("status"),
                    "original_continuation_eligible": row["pair_id"] != "pair-0000"
                    and outcome.get("comparison_eligible") is True
                    and outcome.get("fatal_failure") is False,
                    "original_fatal_failure": boolean(outcome.get("fatal_failure")),
                    "source_refs": [outcome_ref, pair_ref, reporter_ref],
                    "orientation_evidence": "legacy-rotation-scalar-only",
                    "strict_organization_only_claim": False,
                }
            )
        jobs.append(job)
    assessment, assessment_ref = reader.document(root / "NATIVE_OFFICIAL_RESULT_RECHECK.json")
    sidecar = []
    for index, row in enumerate(assessment["records"]):
        sidecar.append(
            {
                "pair_id": row["pair_id"],
                "episode_id": str(row["episode_id"]),
                "arm": "emos",
                "original_raw": boolean(row["raw_structured_pddl_success"]),
                "historical_assessment": boolean(row["verified_official_pddl_success"]),
                "basis": _label(row.get("basis")),
                "status": "retained original assessment; not independently rerun in this import",
                "source_ref": assessment_ref + f"#/records/{index}",
            }
        )
    return (
        jobs,
        runs,
        pairs,
        {
            "source_refs": [
                manifest_ref,
                plan_ref,
                history_ref,
                status_ref,
                final_ref,
                assessment_ref,
                reporter_ref,
            ],
            "populations": {
                "height_man": {
                    "digest": population.digest,
                    "source_ref": plan_ref,
                    "dataset": plan["dataset"],
                    "task": plan["task"],
                    "model_configuration": plan["model_configuration"],
                }
            },
            "original_model_declaration": manifest.get("model"),
            "selection": (
                "complete original manipulation selector; retained and continued runs preserved"
            ),
            "original_limits": {
                k: manifest.get(k) for k in ("official_max_steps", "outcome_retries", "seed")
            },
            "legacy_assessments": sidecar,
            "original_reporter_eligible_pairs": final["comparison_eligible_pairs"],
        },
    )


def historical_details(
    manifest: dict[str, Any], runs: list[dict[str, Any]], pairs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Recompute condition partitions and original-reporter policy without replacing raw facts."""
    by_code = {}
    for code in sorted({r["code_sha"] or "unavailable" for r in runs}):
        by_code[code] = {
            arm: _counts(
                [r for r in runs if (r["code_sha"] or "unavailable") == code and r["arm"] == arm]
            )
            for arm in ("emos", "roboguide")
        }
    eligible = [p for p in pairs if p.get("original_continuation_eligible") is True]
    expected = manifest.get("original_reporter_eligible_pairs")
    if expected is not None and expected != len(eligible):
        raise ValueError("historical reporter policy does not reproduce original eligible count")
    lookup = {r["run_key"]: r for r in runs}
    matrix: Counter[str] = Counter()
    for pair in eligible:
        values = {lookup[k]["arm"]: lookup[k]["official_pddl_success"] for k in pair["runs"]}
        if None not in values.values():
            label = {
                (True, True): "both_true",
                (True, False): "rg_only",
                (False, True): "emos_only",
                (False, False): "both_false",
            }[(values["roboguide"], values["emos"])]
            matrix[label] += 1
    return {
        "primary_by_original_code": by_code,
        "original_reporter_eligible_pairs": expected,
        "original_reporter_both_official_available": sum(matrix.values()),
        "original_reporter_matrix": {
            k: matrix[k] for k in ("both_true", "rg_only", "emos_only", "both_false")
        },
        "historical_assessment": dict(
            Counter(
                "unavailable"
                if r["historical_assessment"] is None
                else str(r["historical_assessment"]).lower()
                for r in manifest.get("legacy_assessments", [])
            )
        ),
        "strict_organization_only_claim": False,
    }


def historical_report(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    """Show a full historical family with per-code conditions and disclosed evidential limits."""
    family, stats = next(iter(summary["families"].items()))
    label = {"navigation": "导航", "height_man": "单层搬运"}[family]
    lines = [
        f"# E1 {label}历史结果包",
        "",
        f"完整计划 {stats['planned_workloads']} 项，保留 {stats['pairs_recorded']} 对原始结果。"
        "未调用模型或仿真，未更改原始结果。",
        "",
        "## 覆盖与官方结果",
        "",
        "| arm | 运行记录 | 成功 | 失败 | 不可用 | Formal / benchmark |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for arm, d in stats["groups"]["primary"].items():
        o = d["official"]
        lines.append(
            f"| {arm} | {d['recorded_runs']} | {o['true']} | {o['false']} | "
            f"{o['unavailable']} | {d['formal_denominator']} / {d['benchmark_denominator']} |"
        )
    lines += [
        "",
        "EMOS 无 RoboGuide admission，分母 0 表示不适用，不是准入被拒。"
        "官方 unavailable 不转成 false。",
        "",
        "## 原始代码条件分组",
        "",
        "原计划的续跑/保留段按实际代码身份分别列出；总计只描述历史覆盖，不是同版本总体性能。",
        "",
        "| 原 launch/运行 SHA | arm | 记录数 | 成功/失败/不可用 | 调用数 | wall time 秒 |",
        "| --- | --- | ---: | --- | ---: | ---: |",
    ]
    for code, arms in summary["historical_details"]["primary_by_original_code"].items():
        for arm, d in arms.items():
            o = d["official"]
            lines.append(
                f"| {code} | {arm} | {d['recorded_runs']} | "
                f"{o['true']}/{o['false']}/{o['unavailable']} | "
                f"{d['provider_calls']['sum']} | {d['elapsed_seconds']['sum']:.3f} |"
            )
    lines += [
        "",
        "## 配对、成本与失败分析",
        "",
        f"- 原逐对 comparison_eligible=true：{stats['recorded_comparison_eligible']}；"
        f"资格未知：{stats['pair_eligibility']['unavailable']}。",
        f"- 原可比标志且双方官方可用：{stats['eligible_both_official_available']}；"
        f"四格：`{json.dumps(stats['paired_official_matrix'], ensure_ascii=False)}`。",
        "- 调用量、token、耗时、失败标签和缺失覆盖见 summary.json 与逐条 runs；"
        "原生声明不冒充返回模型身份。",
        "- 进程起止时刻未记录则为 null，Provider 调用时刻不能替代进程关闭；"
        "耗时累计不是批量并行耗时。",
    ]
    if family == "height_man":
        d = summary["historical_details"]
        lines += [
            "- 旧 continuation 汇总排除 pair-0000，并要求原 fatal_failure=false，"
            f"因此为 {d['original_reporter_eligible_pairs']} 对；"
            "这与原单标志计数不同，两个值均保留。",
            f"- 按该旧汇总口径双方官方可用 {d['original_reporter_both_official_available']} 对；"
            f"四格：`{json.dumps(d['original_reporter_matrix'], ensure_ascii=False)}`。",
            "- 原生历史终态 sidecar 的复核计数："
            f"`{json.dumps(d['historical_assessment'], ensure_ascii=False)}`。"
            "只保留原复核，不回填原始 unavailable，不宣称本轮重做二进制评估。",
        ]
    else:
        lines += [
            "- 导航未找到独立 Pair Validator 资格：不因为两个 arm 都有结果就补写 comparable。"
        ]
    lines += [
        "",
        "## 论文范围与证据缺口",
        "",
        "- 本包完整整理本历史清单，不是新实验，也不声称当前 main 的性能或论文原表精确复现。",
        "- 各条件只有可核对的历史声明；Local How、模型输入及旧初态证据有差异，"
        "不能证明组织层单一因果。",
        "- 部分代码身份是 harness/封装 SHA，不能替代 vendor Git 与已加载模块证据。",
        "- 原始材料仍 local_only，本次消费的元数据摘要不等于视频、数据库和全轨迹的独立备份。",
        "- 原始失败保留；重试/新版本不能回填本历史主结果。",
        "",
    ]
    return "\n".join(lines)


def export_history(bindings: Path, family: str, output: Path, exporter_sha: str) -> dict[str, Any]:
    """Create a fresh portable family revision with unchanged original-byte witnesses."""
    if code_identity(exporter_sha) is None:
        raise ValueError("historical export requires a complete committed exporter SHA")
    local = object_value(json.loads(bindings.read_bytes()))
    reader = ArchiveReader({k: Path(v) for k, v in local["archives"].items()})
    if any(
        output.resolve().is_relative_to(p) or p.is_relative_to(output.resolve())
        for p in reader.roots.values()
    ):
        raise ValueError("historical output overlaps an original archive")
    cfg = object_value(local["history"])
    if family == "navigation":
        jobs, runs, pairs, metadata = _navigation(reader, cfg)
    elif family == "height_man":
        jobs, runs, pairs, metadata = _height(reader, cfg)
    else:
        raise ValueError("unsupported historical family")
    reader.verify_unchanged()
    source_root = Path(cfg["navigation_root" if family == "navigation" else "height_root"])
    manifest = {
        **metadata,
        "cohort_id": source_root.name,
        "revision": "r0001",
        "status": "staging",
        "purpose": "primary",
        "exporter_code_sha": exporter_sha,
        "publication": publication_metadata(local),
        "source_format": "roboguide.e1.historical-family-import/v0.1",
        "report_profile": "historical-family/v0.1",
        "import_mode": "retrospective_read_only",
        "source_code_sha": None,
        "version_policy": (
            "explicit condition groups by original per-run code; no uniform-version overall rate"
        ),
        "raw_backup_status": "local_only",
        "new_provider_simulator_runs": 0,
    }
    seal_package(output, manifest, jobs, runs, pairs, list(reader.artifacts.values()))
    reader.verify_unchanged()
    write_new(
        bindings.parent
        / (source_root.name + "-" + output.name + "-history-local-source-witness.json"),
        encode(
            {
                "sources": {key: str(path) for key, path in reader.paths.items()},
                "original_bytes_unchanged": True,
            }
        ),
    )
    return validate_package(output)
