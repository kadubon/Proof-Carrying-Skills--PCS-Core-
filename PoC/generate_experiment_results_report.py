from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_BOUNDARY_CASES = [
    "reject/proof_len_exceeded",
    "reject/max_inclusion_proofs_exceeded",
    "reject/max_proven_bytes_exceeded",
    "reject/chunk_nonfinal_not_full",
    "reject/mixed_trace_modes",
    "reject/assertion_unproven_event",
    "reject/glue_duplicate_to_path",
    "reject/glue_bounds_exceed_profile",
    "reject/glue_to_path_invalid",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _extract_core_results(e1_log_lines: list[str], e1_summary: dict[str, Any]) -> dict[str, Any]:
    pass_lines = [line for line in e1_log_lines if ": PASS (" in line]
    reject_bundle_pass = [line for line in pass_lines if "\\reject\\" in line and "(bundle)" in line]
    schema_pass = [line for line in pass_lines if "(schema)" in line]

    normalized = [line.replace("\\", "/").lower() for line in pass_lines]
    missing_boundary = []
    for case in REQUIRED_BOUNDARY_CASES:
        key = case.lower()
        if not any(key in line for line in normalized):
            missing_boundary.append(case)

    return {
        "E1_CONFORMANCE": {
            "passed": e1_summary.get("failed", 1) == 0,
            "total": e1_summary.get("total"),
            "failed": e1_summary.get("failed"),
            "bundle_pass": e1_summary.get("bundle_pass"),
            "schema_pass": e1_summary.get("schema_pass"),
        },
        "E2_DETERMINISM": {
            "passed": e1_summary.get("failed", 1) == 0,
            "evidence": e1_summary.get("deterministic_replay_mode"),
        },
        "E3_FAIL_CLOSED": {
            "passed": (e1_summary.get("failed", 1) == 0 and len(reject_bundle_pass) > 0),
            "reject_bundle_pass_count": len(reject_bundle_pass),
        },
        "E4_BOUNDARY": {
            "passed": len(missing_boundary) == 0,
            "missing_cases": missing_boundary,
        },
        "E5_SCHEMA_INTEROP": {
            "passed": (e1_summary.get("failed", 1) == 0 and len(schema_pass) > 0),
            "schema_pass_count": len(schema_pass),
        },
    }


def _extract_e6_results(e6_report: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    scenarios = []
    any_operational_ready = False

    for sc in e6_report.get("scenarios", []):
        agg = sc.get("aggregate", {})
        item = {
            "id": sc.get("id"),
            "mode": sc.get("mode"),
            "status": sc.get("status"),
            "supports_operational_claim": bool(sc.get("supports_operational_claim", False)),
            "campaigns_completed": sc.get("campaigns_completed"),
            "campaigns_planned": sc.get("campaigns_planned"),
            "episodes_per_campaign": sc.get("episodes_per_campaign"),
            "latency_improvement_ratio_mean": agg.get("latency_improvement_ratio", {}).get("mean"),
            "latency_improvement_ratio_ci95": agg.get("latency_improvement_ratio_ci95"),
            "cost_improvement_ratio_mean": agg.get("cost_improvement_ratio", {}).get("mean"),
            "cost_improvement_ratio_ci95": agg.get("cost_improvement_ratio_ci95"),
            "claim_criteria": sc.get("claim_criteria", {}),
            "workload": sc.get("workload"),
            "error": sc.get("error"),
        }
        scenarios.append(item)
        if item["supports_operational_claim"]:
            any_operational_ready = True

    return scenarios, any_operational_ready


def _build_summary(core: dict[str, Any], e6: dict[str, Any], e6_scenarios: list[dict[str, Any]], op_ready: bool) -> dict[str, Any]:
    return {
        "generated_utc": _utc_now(),
        "plan_protocol_version": "2.2.0",
        "core_experiments": core,
        "optional_experiments": {
            "E6_OPTIONAL_COST": {
                "failed_scenarios": e6.get("failed_scenarios", []),
                "scenarios": e6_scenarios,
            }
        },
        "operational_claim": {
            "ready": op_ready,
            "statement": (
                "Operational claim is supported for the disclosed command-mode workload and measured environment."
                if op_ready
                else "Operational claim is not yet supported under current executed scenarios."
            ),
            "scope_limitations": [
                "Applies to the disclosed workload and harness configuration only.",
                "Does not imply universal improvement across all production models/workloads.",
                "PCS-Core correctness/safety claims remain independently gated by E1-E5.",
            ],
        },
        "artifacts": {
            "e1_log": "PoC/runs/e1_conformance.log",
            "e1_summary": "PoC/runs/e1_summary.json",
            "e6_report_json": "PoC/runs/e6_optional_cost_report.json",
            "e6_report_md": "PoC/runs/e6_optional_cost_report.md",
            "e6_campaign_csv": "PoC/runs/e6_optional_cost_campaigns.csv",
            "e6_command_log": "PoC/runs/e6_optional_cost_command_log.jsonl",
            "e6_environment": "PoC/runs/e6_optional_cost_environment_fingerprint.json",
            "workload_disclosure": "PoC/workloads/prod_workload_v1.md",
            "workload_manifest": "PoC/workloads/operational_input_manifest.json",
        },
        "scientific_integrity_notes": [
            "All executed runs are retained in artifacts (including command logs).",
            "E6 is reported as optional/non-gating and separated from PCS-Core correctness claims.",
            "Interpretation is limited to measured evidence and declared workload scope.",
        ],
    }


def _to_md(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Experiment Results Report")
    lines.append("")
    lines.append(f"- Generated (UTC): `{summary['generated_utc']}`")
    lines.append(f"- Protocol version: `{summary['plan_protocol_version']}`")
    lines.append("")

    lines.append("## Core Experiments (E1-E5)")
    lines.append("")
    lines.append("| Experiment | Result | Evidence |")
    lines.append("|---|---|---|")
    core = summary["core_experiments"]
    lines.append(f"| E1_CONFORMANCE | {'PASS' if core['E1_CONFORMANCE']['passed'] else 'FAIL'} | total={core['E1_CONFORMANCE']['total']}, failed={core['E1_CONFORMANCE']['failed']} |")
    lines.append(f"| E2_DETERMINISM | {'PASS' if core['E2_DETERMINISM']['passed'] else 'FAIL'} | {core['E2_DETERMINISM']['evidence']} |")
    lines.append(f"| E3_FAIL_CLOSED | {'PASS' if core['E3_FAIL_CLOSED']['passed'] else 'FAIL'} | reject_bundle_pass_count={core['E3_FAIL_CLOSED']['reject_bundle_pass_count']} |")
    lines.append(f"| E4_BOUNDARY | {'PASS' if core['E4_BOUNDARY']['passed'] else 'FAIL'} | missing_cases={core['E4_BOUNDARY']['missing_cases']} |")
    lines.append(f"| E5_SCHEMA_INTEROP | {'PASS' if core['E5_SCHEMA_INTEROP']['passed'] else 'FAIL'} | schema_pass_count={core['E5_SCHEMA_INTEROP']['schema_pass_count']} |")
    lines.append("")

    lines.append("## Optional Experiment (E6_OPTIONAL_COST)")
    lines.append("")
    e6 = summary["optional_experiments"]["E6_OPTIONAL_COST"]
    lines.append(f"- failed_scenarios: `{e6['failed_scenarios']}`")
    lines.append("")
    for sc in e6["scenarios"]:
        lines.append(f"### Scenario `{sc['id']}`")
        lines.append(f"- mode: `{sc['mode']}`")
        lines.append(f"- status: `{sc['status']}`")
        lines.append(f"- campaigns_completed: `{sc['campaigns_completed']}` / `{sc['campaigns_planned']}`")
        lines.append(f"- episodes_per_campaign: `{sc['episodes_per_campaign']}`")
        lines.append(f"- supports_operational_claim: `{sc['supports_operational_claim']}`")
        lines.append(f"- latency_improvement_ratio_mean: `{sc['latency_improvement_ratio_mean']}`")
        lines.append(f"- latency_improvement_ratio_ci95: `{sc['latency_improvement_ratio_ci95']}`")
        lines.append(f"- cost_improvement_ratio_mean: `{sc['cost_improvement_ratio_mean']}`")
        lines.append(f"- cost_improvement_ratio_ci95: `{sc['cost_improvement_ratio_ci95']}`")
        lines.append("")
        lines.append("#### Claim Criteria")
        for k, v in sc.get("claim_criteria", {}).items():
            lines.append(f"- {k}: `{v}`")
        if sc.get("error"):
            lines.append(f"- error: `{sc['error']}`")
        lines.append("")

    lines.append("## Operational Claim")
    lines.append("")
    lines.append(f"- ready: `{summary['operational_claim']['ready']}`")
    lines.append(f"- statement: {summary['operational_claim']['statement']}")
    lines.append("")
    lines.append("### Scope Limitations")
    for note in summary["operational_claim"]["scope_limitations"]:
        lines.append(f"- {note}")
    lines.append("")

    lines.append("## Scientific Integrity Notes")
    for note in summary["scientific_integrity_notes"]:
        lines.append(f"- {note}")
    lines.append("")

    lines.append("## Artifacts")
    for name, p in summary["artifacts"].items():
        lines.append(f"- {name}: `{p}`")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate consolidated E1-E6 experiment report")
    parser.add_argument("--e1-log", default="PoC/runs/e1_conformance.log")
    parser.add_argument("--e1-summary", default="PoC/runs/e1_summary.json")
    parser.add_argument("--e6-report", default="PoC/runs/e6_optional_cost_report.json")
    parser.add_argument("--out-json", default="PoC/experiment_results_report.json")
    parser.add_argument("--out-md", default="PoC/experiment_results_report.md")
    args = parser.parse_args()

    e1_log_path = Path(args.e1_log)
    e1_summary_path = Path(args.e1_summary)
    e6_report_path = Path(args.e6_report)

    e1_log_lines = e1_log_path.read_text(encoding="utf-8").splitlines()
    e1_summary = _load_json(e1_summary_path)
    e6_report = _load_json(e6_report_path)

    core = _extract_core_results(e1_log_lines, e1_summary)
    e6_scenarios, op_ready = _extract_e6_results(e6_report)
    summary = _build_summary(core, e6_report, e6_scenarios, op_ready)

    Path(args.out_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.out_md).write_text(_to_md(summary), encoding="utf-8")

    print(json.dumps({"out_json": args.out_json, "out_md": args.out_md, "operational_claim_ready": op_ready}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())