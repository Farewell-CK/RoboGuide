"""Recompute and validate portable E1 result packages without running either system."""

from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

from roboguide_eval.publication_io import (
    MAX_PUBLIC_BYTES,
    encode,
    public_safe,
    safe_relative,
    sha256,
    write_new,
    write_rows,
)

SCHEMA = "roboguide.e1.result-publication/v0.1"
RUN_FIELDS = {
    "run_key",
    "job_id",
    "family",
    "episode_id",
    "arm",
    "purpose",
    "execution_mode",
    "original_run_id",
    "original_namespace",
    "source_directory",
    "code_sha",
    "code_identity_basis",
    "system_outcome",
    "official_pddl_success",
    "official_outcome",
    "physical_episode_executed",
    "simulator_steps",
    "admission",
    "elapsed_seconds",
    "provider",
    "failure_codes",
    "failure_stage",
    "retry_of",
    "source_ref",
    "evidence_refs",
    "gaps",
    "started_utc",
    "ended_utc",
}
WORKLOAD_FIELDS = [
    "job_id",
    "family",
    "pair_id",
    "episode_id",
    "scene_id",
    "seed",
    "population_digest",
    "state",
    "roboguide_run",
    "emos_run",
    "gap_code",
    "source_ref",
]


def _counts(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Count original result layers and known measurement coverage independently."""
    official = Counter(row["official_outcome"] for row in runs)
    admissions = [row["admission"] for row in runs if row["admission"] is not None]
    elapsed = [row["elapsed_seconds"] for row in runs if row["elapsed_seconds"] is not None]
    calls = [
        row["provider"]["calls_reported"]
        for row in runs
        if row["provider"]["calls_reported"] is not None
    ]
    costs = [
        row["provider"]["total_tokens"]
        for row in runs
        if row["provider"]["total_tokens"] is not None
    ]
    latencies = [
        row["provider"]["latency_ms_sum"]
        for row in runs
        if row["provider"]["latency_ms_sum"] is not None
    ]
    failure = Counter(code for row in runs for code in row["failure_codes"])
    formal = [
        row for row in runs if (row["admission"] or {}).get("valid_for_formal_population") is True
    ]
    benchmark = [
        row
        for row in runs
        if (row["admission"] or {}).get("valid_for_benchmark_population") is True
    ]
    return {
        "recorded_runs": len(runs),
        "official": {k: official[k] for k in ("true", "false", "unavailable")},
        "system_outcomes": dict(sorted(Counter(row["system_outcome"] for row in runs).items())),
        "fresh_runs": sum(row["execution_mode"] == "fresh" for row in runs),
        "reused_runs": sum(row["execution_mode"] == "reused" for row in runs),
        "physical_episode_executed": sum(row["physical_episode_executed"] is True for row in runs),
        "physical_episode_unknown": sum(row["physical_episode_executed"] is None for row in runs),
        "admission_records": len(admissions),
        "formal_denominator": len(formal),
        "formal_official": {
            k: sum(row["official_outcome"] == k for row in formal)
            for k in ("true", "false", "unavailable")
        },
        "benchmark_denominator": len(benchmark),
        "benchmark_true": sum(row["official_pddl_success"] is True for row in benchmark),
        "elapsed_seconds": {
            "sum": round(sum(elapsed), 6),
            "known": len(elapsed),
            "unknown": len(runs) - len(elapsed),
            "basis": "sum of recorded per-arm wall time; not parallel batch makespan",
        },
        "provider_calls": {
            "sum": sum(calls),
            "known_runs": len(calls),
            "unknown_runs": len(runs) - len(calls),
        },
        "provider_tokens": {
            "sum": sum(costs),
            "known_runs": len(costs),
            "unknown_runs": len(runs) - len(costs),
            "basis": "archived accounting usage only; partial run coverage retained per run",
        },
        "provider_latency_ms": {
            "sum": round(sum(latencies), 6),
            "known_runs": len(latencies),
            "basis": "sum of per-call observed latency, not exclusive wall time",
        },
        "failures_nonexclusive": dict(sorted(failure.items())),
        "code_identities": dict(
            sorted(Counter(row["code_sha"] or "unavailable" for row in runs).items())
        ),
        "requested_models": sorted(
            {m for row in runs for m in row["provider"]["requested_models"]}
        ),
        "reported_models": sorted({m for row in runs for m in row["provider"]["reported_models"]}),
    }


def summarize(
    workloads: list[dict[str, Any]], runs: list[dict[str, Any]], pairs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Derive every published count from original record references without best-of-N selection."""
    lookup = {row["run_key"]: row for row in runs}
    families: dict[str, Any] = {}
    for family in sorted({row["family"] for row in workloads}):
        selected = [row for row in workloads if row["family"] == family]
        observed = [row for row in runs if row["family"] == family]
        eligible = [
            row
            for row in pairs
            if row["family"] == family and row["recorded_comparison_eligible"] is True
        ]
        complete = [
            row
            for row in eligible
            if all(lookup[key]["official_outcome"] != "unavailable" for key in row["runs"])
        ]
        matrix: Counter[str] = Counter()
        for row in complete:
            arms = {lookup[key]["arm"]: lookup[key]["official_pddl_success"] for key in row["runs"]}
            matrix[
                "both_true"
                if all(arms.values())
                else "rg_only"
                if arms["roboguide"]
                else "emos_only"
                if arms["emos"]
                else "both_false"
            ] += 1
        groups = {}
        for purpose in ("primary", "retry", "diagnostic"):
            groups[purpose] = {
                arm: _counts([r for r in observed if r["arm"] == arm and r["purpose"] == purpose])
                for arm in ("emos", "roboguide")
            }
        families[family] = {
            "planned_workloads": len(selected),
            "coverage_states": dict(sorted(Counter(row["state"] for row in selected).items())),
            "primary_arms_without_run": {
                arm: sum(not row[arm + "_run"] for row in selected) for arm in ("emos", "roboguide")
            },
            "groups": groups,
            "pairs_recorded": sum(p["family"] == family for p in pairs),
            "recorded_comparison_eligible": len(eligible),
            "eligible_both_official_available": len(complete),
            "paired_official_matrix": {
                k: matrix[k] for k in ("both_true", "rg_only", "emos_only", "both_false")
            },
        }
    return {
        "schema_version": SCHEMA,
        "families": families,
        "planned_workloads": len(workloads),
        "recorded_arm_references": len(runs),
        "unique_actual_run_keys": len(lookup),
        "historical_versions_combined_rate": None,
        "strict_organization_only_claim": False,
        "measurement_note": "Finite-cohort descriptions, not causal or exact paper reproduction.",
    }


def coverage_csv(summary: dict[str, Any]) -> bytes:
    """Render the phase table only from the machine-readable recomputation."""
    stream = io.StringIO(newline="")
    fields = [
        "family",
        "purpose",
        "arm",
        "planned_workloads",
        "runs",
        "true",
        "false",
        "unavailable",
        "formal",
        "benchmark",
        "fresh",
        "reused",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for family, data in summary["families"].items():
        for purpose, arms in data["groups"].items():
            for arm, values in arms.items():
                writer.writerow(
                    {
                        "family": family,
                        "purpose": purpose,
                        "arm": arm,
                        "planned_workloads": data["planned_workloads"],
                        "runs": values["recorded_runs"],
                        **values["official"],
                        "formal": values["formal_denominator"],
                        "benchmark": values["benchmark_denominator"],
                        "fresh": values["fresh_runs"],
                        "reused": values["reused_runs"],
                    }
                )
    return stream.getvalue().encode()


def validate_records(
    workloads: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    """Refuse identity collisions, invented truth, orphan evidence and replaced primaries."""
    jobs = {row["job_id"]: row for row in workloads}
    lookup = {row["run_key"]: row for row in runs}
    refs = {row["artifact_id"] for row in artifacts}
    if len(jobs) != len(workloads) or len(lookup) != len(runs) or len(refs) != len(artifacts):
        raise ValueError("duplicate publication identity")
    for artifact in artifacts:
        safe_relative(artifact["logical_path"])
        if artifact["available"]:
            if (
                not isinstance(artifact["sha256"], str)
                or len(artifact["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in artifact["sha256"])
            ):
                raise ValueError("invalid original byte digest")
            if type(artifact["size_bytes"]) is not int or artifact["size_bytes"] < 0:
                raise ValueError("invalid original byte size")
        elif artifact["sha256"] is not None or artifact["size_bytes"] is not None:
            raise ValueError("missing artifact has invented bytes")
        if (
            artifact["backup_status"] not in ("local_only", "unavailable")
            or artifact["remote_uri"] is not None
        ):
            raise ValueError("v0.1 local import cannot claim remote backup verification")
    slots: set[tuple[str, str]] = set()
    originals: set[tuple[str, str, str]] = set()
    for row in runs:
        if (
            set(row) != RUN_FIELDS
            or row["job_id"] not in jobs
            or row["arm"] not in ("emos", "roboguide")
        ):
            raise ValueError("invalid run record")
        if row["purpose"] not in ("primary", "retry", "diagnostic") or row[
            "execution_mode"
        ] not in ("fresh", "reused"):
            raise ValueError("unknown run purpose or origin")
        if (
            row["family"] != jobs[row["job_id"]]["family"]
            or row["episode_id"] != jobs[row["job_id"]]["episode_id"]
        ):
            raise ValueError("run detached from workload")
        safe_relative(row["source_directory"])
        origin = (row["original_namespace"], row["source_directory"], row["arm"])
        if origin in originals:
            raise ValueError("original execution has been relabelled as a new run")
        originals.add(origin)
        official = row["official_pddl_success"]
        if official is not None and type(official) is not bool:
            raise ValueError("official result is not a strict boolean")
        if row["official_outcome"] != (
            "unavailable" if official is None else str(official).lower()
        ):
            raise ValueError("official tri-state disagrees with authority")
        if row["retry_of"] is not None:
            original = lookup.get(row["retry_of"])
            if (
                row["purpose"] != "retry"
                or not original
                or original["purpose"] != "primary"
                or any(row[k] != original[k] for k in ("arm", "job_id"))
            ):
                raise ValueError("retry detached from original primary")
        elif row["purpose"] == "retry":
            raise ValueError("retry lacks original source")
        if row["purpose"] == "primary":
            slot = (row["job_id"], row["arm"])
            if slot in slots or jobs[row["job_id"]][row["arm"] + "_run"] != row["run_key"]:
                raise ValueError("duplicate or replaced primary")
            slots.add(slot)
        if row["arm"] == "emos" and row["admission"] is not None:
            raise ValueError("native EMOS has invented RoboGuide admission")
        for ref in [row["source_ref"], *row["evidence_refs"]]:
            if ref.split("#", 1)[0] not in refs:
                raise ValueError("run evidence reference is unresolved")
    for job in workloads:
        if set(job) != set(WORKLOAD_FIELDS) or job["state"] not in (
            "ended",
            "not_started",
            "pre_arm_failure",
            "unknown",
        ):
            raise ValueError("invalid workload coverage record")
        if job["source_ref"].split("#", 1)[0] not in refs:
            raise ValueError("workload plan source is unresolved")
        for arm in ("emos", "roboguide"):
            key = job[arm + "_run"]
            if key and (
                key not in lookup
                or lookup[key]["purpose"] != "primary"
                or lookup[key]["job_id"] != job["job_id"]
            ):
                raise ValueError("workload points at a non-primary or missing run")
        if job["state"] in ("not_started", "pre_arm_failure") and any(
            job[a + "_run"] for a in ("emos", "roboguide")
        ):
            raise ValueError("unexecuted slot has a fabricated run")
    pair_jobs: set[str] = set()
    for pair in pairs:
        if pair["job_id"] in pair_jobs or pair["job_id"] not in jobs:
            raise ValueError("duplicate or unknown pair")
        if pair["family"] != jobs[pair["job_id"]]["family"]:
            raise ValueError("pair detached from workload family")
        if any(ref.split("#", 1)[0] not in refs for ref in pair.get("source_refs", [])):
            raise ValueError("pair source is unresolved")
        pair_jobs.add(pair["job_id"])
        selected = [lookup.get(key) for key in pair["runs"]]
        if len(selected) != 2 or any(
            not row or row["purpose"] != "primary" or row["job_id"] != pair["job_id"]
            for row in selected
        ):
            raise ValueError("pair references invalid primary runs")
        if {row["arm"] for row in selected if row} != {"emos", "roboguide"}:
            raise ValueError("pair does not contain both arms")
        if pair["recorded_comparison_eligible"] and (
            pair["comparability"] != "pair_comparable" or pair["reset_status"] != "matched"
        ):
            raise ValueError("missing comparison evidence became comparable")
    public_safe([workloads, runs, pairs, artifacts])


def report_text(summary: dict[str, Any]) -> str:
    """Generate a Chinese phase report with counts, cost coverage and bounded paper claims."""
    lines = [
        "# E1 首次阶段结果包",
        "",
        "本包是历史冻结条件下的阶段描述，完整保留计划缺口、原始失败与单独重试。",
        "来源选择依据是完整清单、封存状态、逐臂原档、配对和模型记账的可访问性，不按成绩选择。",
        "本次只读导出，未启动 Provider、仿真或新 Mission；原始证据的独立备份尚未落实。",
        "",
        "## 主实验覆盖与结果",
        "",
        "| 类别 | 计划 | 已记录 RG | RG 真/假/不可用 | EMOS 真/假/不可用 | 可用可比对 |",
        "| --- | ---: | ---: | --- | --- | ---: |",
    ]
    for family, values in summary["families"].items():
        arms = values["groups"]["primary"]
        cells = [
            " / ".join(str(arms[a]["official"][k]) for k in ("true", "false", "unavailable"))
            for a in ("roboguide", "emos")
        ]
        lines.append(
            f"| {family} | {values['planned_workloads']} | "
            f"{arms['roboguide']['recorded_runs']} | {cells[0]} | {cells[1]} | "
            f"{values['eligible_both_official_available']} |"
        )
    lines += [
        "",
        "计数为官方 pddl_success；Mission Completed 不替代官方 true。"
        "未执行/执行状态未知不填成 false。",
        "原配对资格与官方可用子集分别保留；旧 reset 为角度标量比较，不升级成完整姿态证明。",
        "",
        "## 配对结果与重试",
        "",
    ]
    for family, values in summary["families"].items():
        lines.append(
            f"- {family}：原记录可比 {values['recorded_comparison_eligible']} 对；"
            f"双方官方可用 {values['eligible_both_official_available']} 对，"
            f"四格计数 {json.dumps(values['paired_official_matrix'], ensure_ascii=False)}。"
        )
        for arm, data in values["groups"]["retry"].items():
            lines.append(
                f"- {family} / {arm} 重试：{data['recorded_runs']} 次，"
                f"{json.dumps(data['official'], ensure_ascii=False)}，不替换主结果。"
            )
    lines += [
        "",
        "## 调用、耗时与失败",
        "",
        "所有数字由 runs 记录重算；完整分布和缺失数量见 summary.json。",
        "耗时为逐臂 wall time 累加，含历史复用，不是本批并行耗时；"
        "token 是已归档 usage，缺失不能按零解释。",
        "",
    ]
    for family, values in summary["families"].items():
        for arm, data in values["groups"]["primary"].items():
            lines.append(
                f"- {family}/{arm}：调用 {data['provider_calls']['sum']}"
                f"（{data['provider_calls']['known_runs']} 次运行有计数）；"
                f"归档 token {data['provider_tokens']['sum']}；"
                f"逐臂耗时累计 {data['elapsed_seconds']['sum']:.3f}s；"
                f"新鲜/复用 {data['fresh_runs']}/{data['reused_runs']}。"
            )
            lines.append(
                "  原始失败标签（可重叠）："
                f"{json.dumps(data['failures_nonexclusive'], ensure_ascii=False)}。"
            )
    lines += [
        "",
        "## 数据支持的结论",
        "",
        "- 可以描述这一冻结条件和完整计划范围内的覆盖、官方成败、调用成本及原记录配对子集差异。",
        "- 原始 SUT/模型失败、官方 unavailable 和未完成计划均可见；重试没有回填主成绩。",
        "- 不能据此证明只改变任务组织导致性能差异，也不等同于原论文精确复现。",
        "",
        "## 限制、假设与待补实验",
        "",
        "- 本批是在兼容修复和后续 Local How 改动之前运行。公开 HSSD 目标身份覆盖"
        "可能影响原生分工；不能把其低分直接归因于组织架构。",
        "- 模型与论文、原生历史复用、Local How/Guard/feedback 差异"
        "和旧 scalar-only 初态证据须披露。",
        "- 索引摘要可在任意机器重算；原档仍为 local_only，"
        "缺少独立备份时不能宣称完整原始结果远端可复现。",
        "- 抓取、复位或检测等失败标签是观察，不自动证明根因；未定位原因保留 unknown。",
        "- 待用户审阅后再确定新冻结版本、完整队列和对照配置，不因本次分析自动重跑或启动批量。",
        "",
    ]
    return "\n".join(lines)


def seal_package(
    root: Path,
    manifest: dict[str, Any],
    workloads: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> None:
    """Write one new complete revision; failure retains staging and never overwrites a revision."""
    if root.exists():
        raise ValueError("publication revision must be a new directory")
    validate_records(workloads, runs, pairs, artifacts)
    root.mkdir(parents=True)
    files = {
        "runs": write_rows(root, "runs", sorted(runs, key=lambda row: row["run_key"])),
        "pairs": write_rows(root, "pairs", sorted(pairs, key=lambda row: row["job_id"])),
        "artifacts": write_rows(
            root, "artifacts", sorted(artifacts, key=lambda row: row["artifact_id"])
        ),
    }
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=WORKLOAD_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted(workloads, key=lambda row: row["job_id"]))
    write_new(root / "workloads.csv", stream.getvalue().encode())
    summary = summarize(workloads, runs, pairs)
    manifest = {**manifest, "schema_version": SCHEMA, "record_files": files}
    manifest["planned_workload_count"] = len(workloads)
    public_safe(manifest)
    write_new(root / "manifest.json", encode(manifest))
    write_new(root / "summary.json", encode(summary))
    write_new(root / "coverage.csv", coverage_csv(summary))
    write_new(root / "REPORT.md", report_text(summary).encode())
    write_new(
        root / "changes.json",
        encode(
            {
                "supersedes_revision": None,
                "reason": "first read-only phase import; no original outcome replaced",
            }
        ),
    )
    checksums = "".join(
        f"{sha256(path.read_bytes())}  {path.name}\n" for path in sorted(root.iterdir())
    )
    write_new(root / "SHA256SUMS", checksums.encode())
    validate_package(root)


def validate_package(root: Path) -> dict[str, Any]:
    """Verify every committed byte, reference and derived result with no original archive access."""
    expected = verify_files(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    if manifest.get("schema_version") != SCHEMA or manifest.get("status") != "staging":
        raise ValueError("unsupported publication generation or invented review status")
    collections = {}
    for kind in ("runs", "pairs", "artifacts"):
        values: list[dict[str, Any]] = []
        names = manifest["record_files"][kind]
        if len(names) != len(set(names)):
            raise ValueError("duplicate record shard")
        for name in names:
            if name not in expected:
                raise ValueError("unbound record shard")
            values.extend(
                json.loads(line)
                for line in (root / safe_relative(name)).read_text().splitlines()
                if line
            )
        collections[kind] = values
    with (root / "workloads.csv").open(newline="") as stream:
        workloads = list(csv.DictReader(stream))
    if manifest.get("planned_workload_count") != len(workloads):
        raise ValueError("planned workload coverage changed")
    validate_records(workloads, collections["runs"], collections["pairs"], collections["artifacts"])
    summary = summarize(workloads, collections["runs"], collections["pairs"])
    if (
        encode(summary) != (root / "summary.json").read_bytes()
        or coverage_csv(summary) != (root / "coverage.csv").read_bytes()
        or report_text(summary).encode() != (root / "REPORT.md").read_bytes()
    ):
        raise ValueError("published aggregate does not recompute from records")
    return {
        "state": "PASS",
        "schema_version": SCHEMA,
        "runs": len(collections["runs"]),
        "workloads": len(workloads),
        "pairs": len(collections["pairs"]),
        "raw_archive_verification": "not performed by portable validation",
    }


def verify_files(root: Path) -> dict[str, str]:
    """Check an exact bounded publication inventory before decoding any derived records."""
    if root.is_symlink():
        raise ValueError("publication root cannot be a symlink")
    checksums = (root / "SHA256SUMS").read_text().splitlines()
    expected = {}
    for line in checksums:
        digest, name = line.split("  ", 1)
        relative = safe_relative(name)
        if name in expected or name == "SHA256SUMS":
            raise ValueError("duplicate or self-referential checksum")
        path = root / relative
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("symlink in publication path")
        raw = path.read_bytes()
        if len(raw) > MAX_PUBLIC_BYTES or sha256(raw) != digest:
            raise ValueError("publication byte integrity failure")
        public_safe(raw.decode())
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if actual != set(expected):
        raise ValueError("unlisted or missing publication file")
    return expected


def main() -> int:
    """Expose a zero-Provider package validator and explicit archived-batch importer."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    check = sub.add_parser("validate")
    check.add_argument("directory", type=Path)
    census = sub.add_parser("census")
    census.add_argument("--bindings", type=Path, required=True)
    census.add_argument("--output", type=Path, required=True)
    census.add_argument("--exporter-sha", required=True)
    census_check = sub.add_parser("validate-census")
    census_check.add_argument("directory", type=Path)
    export = sub.add_parser("export-staged")
    export.add_argument("--bindings", type=Path, required=True)
    export.add_argument("--source-id", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--exporter-sha", required=True)
    arguments = parser.parse_args()
    if arguments.operation == "validate":
        result = validate_package(arguments.directory)
    elif arguments.operation in ("census", "validate-census"):
        from roboguide_eval.e1_census import export_census, validate_census

        result = (
            export_census(arguments.bindings, arguments.output, arguments.exporter_sha)
            if arguments.operation == "census"
            else validate_census(arguments.directory)
        )
    else:
        from roboguide_eval.e1_staged_import import export_staged

        result = export_staged(
            arguments.bindings, arguments.source_id, arguments.output, arguments.exporter_sha
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
