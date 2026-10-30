"""Recompute the four-family entry table from explicitly indexed immutable publications."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from roboguide_eval.e1_publication import validate_package, verify_files
from roboguide_eval.publication_io import code_identity, encode, safe_relative, sha256, write_new

FAMILIES = {
    "navigation": "导航",
    "height_man": "单层搬运",
    "perception": "感知",
    "rearrangement": "多机器人重排",
}


def overview_inputs(
    base: Path, entries: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Recheck source revisions and refuse duplicate families or changed manifests."""
    families: dict[str, Any] = {}
    sources = []
    for entry in entries:
        if entry["kind"] != "cohort":
            continue
        root = base / safe_relative(entry["path"])
        raw = (root / "manifest.json").read_bytes()
        if sha256(raw) != entry["manifest_sha256"]:
            raise ValueError("overview source manifest changed")
        validate_package(root)
        manifest = json.loads(raw)
        summary = json.loads((root / "summary.json").read_bytes())
        for family, stats in summary["families"].items():
            if family in families or family not in FAMILIES:
                raise ValueError("duplicate or unknown family in four-family overview")
            families[family] = {
                "source_revision": entry["path"],
                "source_manifest_sha256": entry["manifest_sha256"],
                "statistics": stats,
                "original_model_declaration": manifest.get("original_model_declaration"),
                "condition_details": summary.get("historical_details"),
            }
        sources.append(
            {"kind": "cohort", "path": entry["path"], "manifest_sha256": entry["manifest_sha256"]}
        )
    if set(families) != set(FAMILIES):
        raise ValueError("four-family overview requires all four actual source packages")
    return {
        "schema_version": "roboguide.e1.four-family-overview/v0.1",
        "families": families,
        "planned_workload_coverage": sum(
            d["statistics"]["planned_workloads"] for d in families.values()
        ),
        "combined_success_rate": None,
        "uniform_code_or_configuration": False,
        "raw_backup_status": "local_only",
        "new_provider_simulator_runs": 0,
    }, sources


def overview_csv(summary: dict[str, Any]) -> bytes:
    """Render every family's primary results with explicit source and denominator fields."""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=[
            "family",
            "source_revision",
            "planned_workloads",
            "recorded_pairs",
            "arm",
            "runs",
            "true",
            "false",
            "unavailable",
            "formal_denominator",
            "benchmark_denominator",
            "raw_eligible_pairs",
            "eligible_both_official_available",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for family in FAMILIES:
        group = summary["families"][family]
        stats = group["statistics"]
        for arm, values in stats["groups"]["primary"].items():
            writer.writerow(
                {
                    "family": family,
                    "source_revision": group["source_revision"],
                    "planned_workloads": stats["planned_workloads"],
                    "recorded_pairs": stats["pairs_recorded"],
                    "arm": arm,
                    "runs": values["recorded_runs"],
                    **values["official"],
                    "formal_denominator": values["formal_denominator"],
                    "benchmark_denominator": values["benchmark_denominator"],
                    "raw_eligible_pairs": stats["recorded_comparison_eligible"]
                    if stats["pair_eligibility"]["unavailable"] == 0
                    else "unavailable",
                    "eligible_both_official_available": stats["eligible_both_official_available"]
                    if stats["pair_eligibility"]["unavailable"] == 0
                    else "unavailable",
                }
            )
    return stream.getvalue().encode()


def overview_report(summary: dict[str, Any]) -> str:
    """Present all four families while keeping versions, assessments and trial purposes distinct."""
    lines = [
        "# E1 四类阶段结果总表",
        "",
        "四类均已有逐项计划、逐臂运行、配对引用、代码/模型证据和可复算统计。"
        "导航与单层搬运来自较早历史批次，感知和重排来自后续冻结批次；"
        "不能将它们称为同一版本的完整评测。",
        "",
        "| 类别 | 计划 | 双方结果 | RG 成/败/未知 | EMOS 成/败/未知 | 原可比且真值可用 |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    for family, label in FAMILIES.items():
        group = summary["families"][family]
        stats = group["statistics"]
        arms = stats["groups"]["primary"]
        cells = [
            " / ".join(str(arms[a]["official"][k]) for k in ("true", "false", "unavailable"))
            for a in ("roboguide", "emos")
        ]
        paired = (
            stats["eligible_both_official_available"]
            if stats["pair_eligibility"]["unavailable"] == 0
            else "资格未知"
        )
        lines.append(
            f"| {label} | {stats['planned_workloads']} | {stats['pairs_recorded']} | "
            f"{cells[0]} | {cells[1]} | {paired} |"
        )
    height = summary["families"]["height_man"]["statistics"]
    details = summary["families"]["height_man"]["condition_details"] or {}
    assessed = details.get("historical_assessment", {})
    recorded = height["groups"]["primary"]["emos"]["official"]
    perception = summary["families"]["perception"]["statistics"]
    states = perception["coverage_states"]
    lines += [
        "",
        f"四类计划覆盖合计 {summary['planned_workload_coverage']} 项。"
        "合计只用于覆盖清点，不生成四类混合版本总成功率。",
        "",
        "## 每类的来源、版本、模型与证据",
        "",
    ]
    for family, label in FAMILIES.items():
        group = summary["families"][family]
        stats = group["statistics"]
        arms = stats["groups"]["primary"]
        lines += [
            f"### {label}",
            "",
            f"- 发布修订：`{group['source_revision']}`；"
            "从该目录的 workloads.csv、runs/pairs 分片和 summary.json 可追到原档摘要。",
        ]
        for arm, d in arms.items():
            models = d["requested_models"] or [group["original_model_declaration"] or "unavailable"]
            lines.append(
                f"- {arm}：原始代码计数 `{json.dumps(d['code_identities'], ensure_ascii=False)}`；"
                f"请求模型 `{json.dumps(models)}`，返回模型 `{json.dumps(d['reported_models'])}`。"
            )
            lines.append(
                f"- {arm}：归档调用计数 {d['provider_calls']['sum']}"
                f"（{d['provider_calls']['known_runs']} 项有计数），"
                f"归档 token {d['provider_tokens']['sum']}；"
                f"逐臂 wall time 累计 {d['elapsed_seconds']['sum']:.3f}s，"
                f"{d['elapsed_seconds']['unknown']} 项耗时未知。"
            )
        retry = sum(d["recorded_runs"] for d in stats["groups"]["retry"].values())
        diag = sum(d["recorded_runs"] for d in stats["groups"]["diagnostic"].values())
        lines.append(f"- 主结果之外另有 retry {retry}、diagnostic {diag} 条运行，均不回填主成绩。")
    lines += [
        "",
        "## 口径与论文使用",
        "",
        "- 导航原报告没有独立配对资格，保留 unknown；各臂完整原计划范围内的官方 true "
        "计数不能因此称为严格配对收益。",
        f"- 搬运原生原始结构化结果 `{json.dumps(recorded, ensure_ascii=False)}` "
        f"与旧 sidecar `{json.dumps(assessed, ensure_ascii=False)}` 分别保留，没有替换。",
        f"- 搬运原逐对可比标志为 {height['recorded_comparison_eligible']}；"
        "旧 continuation 汇总排除 pair-0000 且要求 fatal_failure=false，"
        f"得到 {details.get('original_reporter_eligible_pairs')}。按各自原始口径记录。",
        f"- 感知计划 {perception['planned_workloads']} 项，"
        f"有 {states.get('pre_arm_failure', 0)} 项 pre-arm failure、"
        f"{states.get('not_started', 0)} 项未派发，不能记为官方 false。",
        "- 代码、模型和配置属于不同历史条件；成本为已归档调用/usage 与逐臂耗时，"
        "不能等同账单或批量并行耗时。",
        "- 可以用来描述已执行的覆盖、原始官方结果、已归档调用/耗时、失败模式和证据缺口。"
        "尚不支持当前 main 性能、原论文精确复现或仅组织层单一因果结论。",
        "- 旧感知/重排快照仍在原四类清点中独立保留；本表没有将它们与后续结果相加，"
        "也没有按成绩挑选每个 episode 的最好一次。",
        "- 原档仍 local_only，完整物理初态等价、已加载原生模块身份和独立备份缺口"
        "未因四类整理完成而消失。",
        "",
    ]
    return "\n".join(lines)


def export_overview(base: Path, output: Path, exporter_sha: str) -> dict[str, Any]:
    """Seal a new cross-family entry revision only after validating all referenced packages."""
    if output.exists():
        raise ValueError("overview revision must be new")
    if code_identity(exporter_sha) is None:
        raise ValueError("overview requires a complete exporter identity")
    index = json.loads((base / "index.json").read_bytes())
    summary, sources = overview_inputs(base, index["entries"])
    output.mkdir(parents=True)
    for name, raw in [
        (
            "manifest.json",
            encode(
                {
                    "schema_version": summary["schema_version"],
                    "source_revisions": sources,
                    "exporter_code_sha": exporter_sha,
                    "status": "staging",
                }
            ),
        ),
        ("summary.json", encode(summary)),
        ("coverage.csv", overview_csv(summary)),
        ("REPORT.md", overview_report(summary).encode()),
    ]:
        write_new(output / name, raw)
    write_new(
        output / "SHA256SUMS",
        "".join(f"{sha256(p.read_bytes())}  {p.name}\n" for p in sorted(output.iterdir())).encode(),
    )
    return validate_overview(base, output)


def validate_overview(base: Path, root: Path) -> dict[str, Any]:
    """Recompute all four families from exact immutable source revisions, without raw archives."""
    verify_files(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    if manifest.get("schema_version") != "roboguide.e1.four-family-overview/v0.1":
        raise ValueError("unsupported overview schema")
    summary, _ = overview_inputs(base, manifest["source_revisions"])
    if (
        (root / "summary.json").read_bytes() != encode(summary)
        or (root / "coverage.csv").read_bytes() != overview_csv(summary)
        or (root / "REPORT.md").read_bytes() != overview_report(summary).encode()
    ):
        raise ValueError("four-family overview does not recompute")
    return {
        "state": "PASS",
        "families": len(summary["families"]),
        "planned_workloads": summary["planned_workload_coverage"],
        "combined_success_rate": None,
    }
