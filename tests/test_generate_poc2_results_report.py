from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_campaign_csv(path: Path) -> None:
    rows = [
        {
            "N_reuse": 1,
            "pass": True,
            "baseline_total_latency_ms": 100.0,
            "pcs_total_latency_ms": 120.0,
            "baseline_total_cost": 0.10,
            "pcs_total_cost": 0.12,
            "baseline_total_cost_token": 0.001,
            "pcs_total_cost_token": 0.002,
            "cost_scenario": "C1_equal_cost",
            "fixed_overhead_id": "F0_zero",
            "command_failures": 0,
            "retry_attempts": 0,
        },
        {
            "N_reuse": 3,
            "pass": True,
            "baseline_total_latency_ms": 300.0,
            "pcs_total_latency_ms": 150.0,
            "baseline_total_cost": 0.30,
            "pcs_total_cost": 0.15,
            "baseline_total_cost_token": 0.003,
            "pcs_total_cost_token": 0.002,
            "cost_scenario": "C1_equal_cost",
            "fixed_overhead_id": "F0_zero",
            "command_failures": 0,
            "retry_attempts": 0,
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def test_generate_poc2_results_report(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    tag = "demo"

    _write_json(runs_dir / f"{tag}_final_report.json", {"hard_pass": True, "hard_gates": {"P1_CONFORMANCE_SMOKE": True}})
    _write_json(runs_dir / f"{tag}_p2_summary.json", {"latency_improvement": {"mean": 0.1}})
    _write_json(runs_dir / f"{tag}_protocol_fingerprint.json", {"config_sha256": "x"})
    _write_campaign_csv(runs_dir / f"{tag}_campaigns.csv")

    out_json = tmp_path / "report.json"
    out_md = tmp_path / "report.md"
    cp = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "PoC2/generate_poc2_results_report.py"),
            "--runs-dir",
            str(runs_dir),
            "--tags",
            tag,
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stderr
    assert out_json.exists()
    assert out_md.exists()

    obj = json.loads(out_json.read_text(encoding="utf-8"))
    assert obj["run_tags"] == [tag]
    assert "threshold_analysis" in obj
    assert "claim_readout" in obj
