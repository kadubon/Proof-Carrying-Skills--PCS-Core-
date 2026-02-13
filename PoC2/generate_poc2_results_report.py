#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(value: Any) -> float:
    try:
        x = float(value)
        return x
    except Exception:
        return float("nan")


def to_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def bootstrap_ci_improvement(
    baseline: list[float], pcs: list[float], n_boot: int = 5000, alpha: float = 0.05, seed: int = 1234
) -> dict[str, float]:
    if len(baseline) != len(pcs) or len(baseline) == 0:
        return {"mean": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": 0.0}
    rng = random.Random(seed)
    idx = list(range(len(baseline)))
    vals: list[float] = []
    for _ in range(max(100, n_boot)):
        sample = [rng.choice(idx) for _ in idx]
        b = sum(baseline[i] for i in sample)
        p = sum(pcs[i] for i in sample)
        if b <= 0:
            continue
        vals.append(1.0 - (p / b))
    if not vals:
        return {"mean": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": 0.0}
    vals.sort()
    lo_i = int((alpha / 2.0) * (len(vals) - 1))
    hi_i = int((1 - alpha / 2.0) * (len(vals) - 1))
    return {
        "mean": float(sum(vals) / len(vals)),
        "ci_low": float(vals[lo_i]),
        "ci_high": float(vals[hi_i]),
        "n": float(len(vals)),
    }


def load_campaign_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rec = dict(row)
            rec["pass"] = to_bool(row.get("pass"))
            rec["N_reuse"] = to_int(row.get("N_reuse"))
            rec["baseline_total_latency_ms"] = to_float(row.get("baseline_total_latency_ms"))
            rec["pcs_total_latency_ms"] = to_float(row.get("pcs_total_latency_ms"))
            rec["baseline_total_cost"] = to_float(row.get("baseline_total_cost"))
            rec["pcs_total_cost"] = to_float(row.get("pcs_total_cost"))
            rec["baseline_total_cost_token"] = to_float(row.get("baseline_total_cost_token"))
            rec["pcs_total_cost_token"] = to_float(row.get("pcs_total_cost_token"))
            rec["command_failures"] = to_int(row.get("command_failures"))
            rec["retry_attempts"] = to_int(row.get("retry_attempts"))
            rows.append(rec)
    return rows


def finite(x: float) -> bool:
    return not (math.isnan(x) or math.isinf(x))


def summarize_rows(rows: list[dict[str, Any]], seed_base: int = 1000) -> dict[str, Any]:
    passed = [r for r in rows if r.get("pass")]
    lat_b = [r["baseline_total_latency_ms"] for r in passed if finite(r["baseline_total_latency_ms"]) and finite(r["pcs_total_latency_ms"])]
    lat_p = [r["pcs_total_latency_ms"] for r in passed if finite(r["baseline_total_latency_ms"]) and finite(r["pcs_total_latency_ms"])]

    cost_b = [r["baseline_total_cost"] for r in passed if finite(r["baseline_total_cost"]) and finite(r["pcs_total_cost"])]
    cost_p = [r["pcs_total_cost"] for r in passed if finite(r["baseline_total_cost"]) and finite(r["pcs_total_cost"])]

    tok_b = [r["baseline_total_cost_token"] for r in passed if finite(r["baseline_total_cost_token"]) and finite(r["pcs_total_cost_token"])]
    tok_p = [r["pcs_total_cost_token"] for r in passed if finite(r["baseline_total_cost_token"]) and finite(r["pcs_total_cost_token"])]

    total_failures = sum(to_int(r.get("command_failures")) for r in rows)
    total_retries = sum(to_int(r.get("retry_attempts")) for r in rows)
    total_ops_est = sum((2 * to_int(r.get("N_reuse")) + 1) for r in rows)

    latency = bootstrap_ci_improvement(lat_b, lat_p, n_boot=5000, seed=seed_base + 1)
    cost_ms = bootstrap_ci_improvement(cost_b, cost_p, n_boot=5000, seed=seed_base + 2)
    cost_token = bootstrap_ci_improvement(tok_b, tok_p, n_boot=5000, seed=seed_base + 3)

    return {
        "rows": len(rows),
        "pass_rows": len(passed),
        "latency_improvement": latency,
        "cost_improvement_ms": cost_ms,
        "cost_improvement_token": cost_token,
        "failure_rate_estimated": (total_failures / total_ops_est) if total_ops_est > 0 else float("nan"),
        "retry_avg_per_operation_estimated": (total_retries / total_ops_est) if total_ops_est > 0 else float("nan"),
    }


def group_by(rows: list[dict[str, Any]], keys: list[str]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    out: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for r in rows:
        k = tuple(r.get(key) for key in keys)
        out.setdefault(k, []).append(r)
    return out


def condition_positive(ci: dict[str, Any]) -> bool:
    return finite(to_float(ci.get("ci_low"))) and to_float(ci.get("ci_low")) > 0.0


def unique_sorted_str(values: list[Any]) -> list[str]:
    return sorted({str(v) for v in values if v not in (None, "", "None")})


def unique_sorted_int(values: list[Any]) -> list[int]:
    out: set[int] = set()
    for v in values:
        if v in (None, "", "None"):
            continue
        try:
            out.add(int(float(v)))
        except Exception:
            continue
    return sorted(out)


def derive_thresholds(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_n = group_by(rows, ["N_reuse"])
    n_summaries: list[dict[str, Any]] = []
    for (n,), sub in sorted(by_n.items(), key=lambda x: int(x[0][0])):
        s = summarize_rows(sub, seed_base=2000 + int(n) * 10)
        n_summaries.append({"N_reuse": int(n), **s})

    threshold_ms = None
    threshold_all = None
    for s in n_summaries:
        if condition_positive(s["latency_improvement"]) and condition_positive(s["cost_improvement_ms"]) and threshold_ms is None:
            threshold_ms = int(s["N_reuse"])
        if (
            condition_positive(s["latency_improvement"])
            and condition_positive(s["cost_improvement_ms"])
            and condition_positive(s["cost_improvement_token"])
            and threshold_all is None
        ):
            threshold_all = int(s["N_reuse"])

    by_n_scenario = group_by(rows, ["N_reuse", "cost_scenario"])
    scenario_matrix: list[dict[str, Any]] = []
    for (n, scenario), sub in sorted(by_n_scenario.items(), key=lambda x: (int(x[0][0]), str(x[0][1]))):
        s = summarize_rows(sub, seed_base=3000 + int(n) * 100 + (0 if scenario == "C1_equal_cost" else 1))
        scenario_matrix.append({"N_reuse": int(n), "cost_scenario": str(scenario), **s})

    return {
        "by_n_reuse": n_summaries,
        "by_n_reuse_and_scenario": scenario_matrix,
        "threshold_ms_model_min_n": threshold_ms,
        "threshold_all_models_min_n": threshold_all,
    }


def build_report(run_tags: list[str], run_data: list[dict[str, Any]], combined_rows: list[dict[str, Any]]) -> dict[str, Any]:
    per_run = []
    for rd in run_data:
        per_run.append(
            {
                "tag": rd["tag"],
                "hard_pass": rd["final"].get("hard_pass"),
                "hard_gates": rd["final"].get("hard_gates"),
                "p2_summary": rd["p2"],
                "protocol_fingerprint": rd["protocol"],
                "stage_id": rd.get("stage_id", "UNSPECIFIED"),
                "summary_from_campaign_rows": summarize_rows(rd["rows"], seed_base=1100),
            }
        )

    combined_summary = summarize_rows(combined_rows, seed_base=5000)
    thresholds = derive_thresholds(combined_rows)
    stage_ids = unique_sorted_str([rd.get("stage_id") for rd in run_data]) or ["UNSPECIFIED"]

    by_stage = group_by(combined_rows, ["_stage_id"])
    threshold_stability_by_stage: list[dict[str, Any]] = []
    for (stage_id,), sub in sorted(by_stage.items(), key=lambda x: str(x[0][0])):
        sid_text = str(stage_id)
        sid_seed = sum(ord(ch) for ch in sid_text) % 1000
        s = summarize_rows(sub, seed_base=7000 + sid_seed)
        t = derive_thresholds(sub)
        threshold_stability_by_stage.append(
            {
                "stage_id": sid_text,
                "rows": s["rows"],
                "pass_rows": s["pass_rows"],
                "threshold_ms_model_min_n": t["threshold_ms_model_min_n"],
                "threshold_all_models_min_n": t["threshold_all_models_min_n"],
                "latency_ci_low": s["latency_improvement"]["ci_low"],
                "cost_ms_ci_low": s["cost_improvement_ms"]["ci_low"],
                "cost_token_ci_low": s["cost_improvement_token"]["ci_low"],
            }
        )

    all_ms_positive = all(
        condition_positive(r["summary_from_campaign_rows"]["latency_improvement"])
        and condition_positive(r["summary_from_campaign_rows"]["cost_improvement_ms"])
        for r in per_run
    )
    all_token_positive = all(condition_positive(r["summary_from_campaign_rows"]["cost_improvement_token"]) for r in per_run)

    claim = {
        "can_claim_ms_model_effective_in_scope": bool(all_ms_positive and thresholds["threshold_ms_model_min_n"] is not None),
        "ms_model_threshold_min_n_reuse": thresholds["threshold_ms_model_min_n"],
        "can_claim_token_model_effective_in_scope": bool(all_token_positive and thresholds["threshold_all_models_min_n"] is not None),
        "token_model_threshold_min_n_reuse": thresholds["threshold_all_models_min_n"],
        "scope": {
            "stage_profile": stage_ids[0] if len(stage_ids) == 1 else "MULTI_STAGE",
            "stage_profiles_included": stage_ids,
            "workload_ids_included": unique_sorted_str([r.get("workload_id") for r in combined_rows]),
            "complexity_tiers_included": unique_sorted_str([r.get("complexity_tier") for r in combined_rows]),
            "concurrency_levels_included": unique_sorted_int([r.get("concurrency") for r in combined_rows]),
            "reuse_counts_tested": unique_sorted_int([r.get("N_reuse") for r in combined_rows]),
            "cost_scenarios_included": unique_sorted_str([r.get("cost_scenario") for r in combined_rows]),
            "fixed_overheads_included": unique_sorted_str([r.get("fixed_overhead_id") for r in combined_rows]),
        },
        "non_claims": [
            "No universal gain claim beyond tested stage profile and workload.",
            "No semantic quality improvement claim.",
            "Token-model gain is not claimed unless token CI lower bounds become positive.",
        ],
    }

    return {
        "generated_utc": utc_now(),
        "run_tags": run_tags,
        "runs": per_run,
        "combined_summary": combined_summary,
        "threshold_analysis": thresholds,
        "threshold_stability_by_stage": threshold_stability_by_stage,
        "claim_readout": claim,
        "scientific_honesty_notes": [
            "All configured runs are included; no selective exclusion of negative regions.",
            "ABBA crossover and fixed prompt settings reduce but do not eliminate network/provider drift.",
            "Thresholds are conditional on the tested workload and cost assumptions.",
        ],
    }


def render_md(report: dict[str, Any]) -> str:
    lines: list[str] = []
    scope = report["claim_readout"]["scope"]
    stages = scope.get("stage_profiles_included") or []
    if len(stages) == 1:
        title = f"PoC2 {stages[0]} Results Report"
    elif stages:
        title = f"PoC2 Multi-Stage Results Report ({', '.join(stages)})"
    else:
        title = "PoC2 Results Report"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- Generated (UTC): `{report['generated_utc']}`")
    lines.append(f"- Run tags: `{', '.join(report['run_tags'])}`")
    lines.append("")

    lines.append("## Per-Run Summary")
    lines.append("")
    lines.append("| tag | hard_pass | latency mean | latency CI low | cost(ms) mean | cost(ms) CI low | cost(token) mean | cost(token) CI low |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for run in report["runs"]:
        s = run["summary_from_campaign_rows"]
        lines.append(
            f"| {run['tag']} | {run['hard_pass']} | "
            f"{s['latency_improvement']['mean']:.4f} | {s['latency_improvement']['ci_low']:.4f} | "
            f"{s['cost_improvement_ms']['mean']:.4f} | {s['cost_improvement_ms']['ci_low']:.4f} | "
            f"{s['cost_improvement_token']['mean']:.4f} | {s['cost_improvement_token']['ci_low']:.4f} |"
        )
    lines.append("")

    c = report["combined_summary"]
    lines.append("## Combined Summary (All Runs)")
    lines.append("")
    lines.append(f"- rows: `{c['rows']}` (pass_rows: `{c['pass_rows']}`)")
    lines.append(f"- latency improvement: mean `{c['latency_improvement']['mean']:.4f}`, 95% CI low `{c['latency_improvement']['ci_low']:.4f}`")
    lines.append(f"- cost improvement (ms model): mean `{c['cost_improvement_ms']['mean']:.4f}`, 95% CI low `{c['cost_improvement_ms']['ci_low']:.4f}`")
    lines.append(f"- cost improvement (token model): mean `{c['cost_improvement_token']['mean']:.4f}`, 95% CI low `{c['cost_improvement_token']['ci_low']:.4f}`")
    lines.append(f"- failure_rate_estimated: `{c['failure_rate_estimated']}`")
    lines.append(f"- retry_avg_per_operation_estimated: `{c['retry_avg_per_operation_estimated']}`")
    lines.append("")

    lines.append("## Threshold Analysis")
    lines.append("")
    lines.append(f"- ms-model threshold min N_reuse: `{report['threshold_analysis']['threshold_ms_model_min_n']}`")
    lines.append(f"- all-model threshold min N_reuse (latency + ms + token): `{report['threshold_analysis']['threshold_all_models_min_n']}`")
    lines.append("")
    lines.append("| N_reuse | latency CI low > 0 | cost(ms) CI low > 0 | cost(token) CI low > 0 |")
    lines.append("|---:|---:|---:|---:|")
    for s in report["threshold_analysis"]["by_n_reuse"]:
        lat = s["latency_improvement"]["ci_low"] > 0
        ms = s["cost_improvement_ms"]["ci_low"] > 0
        tok = s["cost_improvement_token"]["ci_low"] > 0
        lines.append(f"| {s['N_reuse']} | {lat} | {ms} | {tok} |")
    lines.append("")

    lines.append("## Threshold Stability By Stage")
    lines.append("")
    lines.append("| stage_id | rows | pass_rows | ms threshold min N | all-model threshold min N |")
    lines.append("|---|---:|---:|---:|---:|")
    for s in report.get("threshold_stability_by_stage", []):
        lines.append(
            f"| {s['stage_id']} | {s['rows']} | {s['pass_rows']} | "
            f"{s['threshold_ms_model_min_n']} | {s['threshold_all_models_min_n']} |"
        )
    lines.append("")

    claim = report["claim_readout"]
    lines.append("## Claim Readout")
    lines.append("")
    lines.append(f"- can_claim_ms_model_effective_in_scope: `{claim['can_claim_ms_model_effective_in_scope']}`")
    lines.append(f"- can_claim_token_model_effective_in_scope: `{claim['can_claim_token_model_effective_in_scope']}`")
    lines.append(f"- scope: `{claim['scope']}`")
    lines.append("")
    lines.append("### Non-Claims")
    for n in claim["non_claims"]:
        lines.append(f"- {n}")
    lines.append("")

    lines.append("## Scientific Honesty Notes")
    for note in report["scientific_honesty_notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate PoC2 combined results report from run tags.")
    parser.add_argument("--runs-dir", default="PoC2/runs")
    parser.add_argument("--tags", nargs="+", required=True, help="run tags, e.g. poc2_s1_pilot_run1 poc2_s1_pilot_run2")
    parser.add_argument("--out-json", default="PoC2/poc2_s1_results_report.json")
    parser.add_argument("--out-md", default="PoC2/poc2_s1_results_report.md")
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir).resolve()
    combined_rows: list[dict[str, Any]] = []
    run_data: list[dict[str, Any]] = []

    for tag in args.tags:
        final_path = runs_dir / f"{tag}_final_report.json"
        p2_path = runs_dir / f"{tag}_p2_summary.json"
        campaigns_path = runs_dir / f"{tag}_campaigns.csv"
        protocol_path = runs_dir / f"{tag}_protocol_fingerprint.json"
        effective_config_path = runs_dir / f"{tag}_effective_config.json"
        if not (final_path.exists() and p2_path.exists() and campaigns_path.exists() and protocol_path.exists()):
            raise FileNotFoundError(f"missing artifacts for tag: {tag}")
        stage_id = "UNSPECIFIED"
        if effective_config_path.exists():
            effective_cfg = load_json(effective_config_path)
            stage_id = effective_cfg.get("_stage_meta", {}).get("stage_id") or effective_cfg.get("artifact_prefix") or "UNSPECIFIED"
        rows = load_campaign_rows(campaigns_path)
        for r in rows:
            r["_tag"] = tag
            r["_stage_id"] = stage_id
        combined_rows.extend(rows)
        run_data.append(
            {
                "tag": tag,
                "stage_id": stage_id,
                "final": load_json(final_path),
                "p2": load_json(p2_path),
                "protocol": load_json(protocol_path),
                "rows": rows,
            }
        )

    report = build_report(args.tags, run_data, combined_rows)
    Path(args.out_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.out_md).write_text(render_md(report), encoding="utf-8")

    print(json.dumps({"out_json": args.out_json, "out_md": args.out_md}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
