from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_helper(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "import argparse, json",
                "p = argparse.ArgumentParser()",
                "p.add_argument('--mode', required=True)",
                "p.add_argument('--bundle', default='')",
                "p.add_argument('--episode', type=int, default=0)",
                "args = p.parse_args()",
                "if args.mode == 'conformance':",
                "    print('total=1 failed=0')",
                "elif args.mode == 'verify':",
                "    print('ok')",
                "elif args.mode in ('baseline', 'pcs-run', 'pcs-verify'):",
                "    print(json.dumps({'mode': args.mode, 'episode': args.episode}, sort_keys=True))",
                "else:",
                "    raise SystemExit(2)",
            ]
        ),
        encoding="utf-8",
    )


def _write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "manifest_version": "1.0.0",
                "workload_id": "test_workload",
                "shared_input": "hello",
                "episodes": [{"episode_idx": 0, "reuse_tag": "r0"}, {"episode_idx": 1, "reuse_tag": "r1"}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _base_config(helper: Path, manifest: Path, out_dir: Path) -> dict[str, object]:
    helper_posix = helper.as_posix()
    manifest_posix = manifest.as_posix()
    return {
        "artifact_prefix": "poc2",
        "repo_root": ".",
        "paths": {"operational_cache_dir": out_dir.as_posix() + "/cache"},
        "execution": {"per_episode_timeout_sec": 30},
        "commands": {
            "conformance": f"python {helper_posix} --mode conformance",
            "verifier_template": f"python {helper_posix} --mode verify --bundle {{bundle}}",
        },
        "workload": {"backend": "command"},
        "workloads": {
            "families": [
                {
                    "id": "W1",
                    "enabled": True,
                    "manifest": manifest_posix,
                    "commands": {
                        "baseline": f"python {helper_posix} --mode baseline --episode {{episode_idx}}",
                        "pcs_run": f"python {helper_posix} --mode pcs-run",
                        "pcs_verify": f"python {helper_posix} --mode pcs-verify --episode {{episode_idx}}",
                    },
                }
            ],
            "complexity_profiles": {"light": {"enabled": True, "baseline_iterations": 1, "run_iterations": 1, "verify_iterations": 1}},
        },
        "experiment": {
            "concurrency_levels": [1],
            "reuse_counts_N": [2],
            "campaign_repetitions_per_cell": 1,
            "warmup_episodes": 0,
            "random_seeds": [101],
            "order_seed": 123,
            "max_cells": 0,
        },
        "cost_model": {
            "c_run_per_ms": 0.000001,
            "c_hash_per_op": 0.0,
            "input_token_usd": 0.000001,
            "output_token_usd": 0.000001,
            "fallback_ms_cost_if_usage_missing": 0.000001,
            "scenarios": [{"id": "S1", "c_check_to_c_run_ratio": 1.0}],
            "fixed_overheads": [{"id": "F1", "c_registry_fixed": 0.0, "c_cert_fixed": 0.0}],
        },
        "analysis": {"bootstrap_samples": 100},
        "stress_fail_closed": {"injections": [{"id": "i1", "bundle": "dummy", "expected_reject": False}]},
        "determinism": {"sample_accept_bundles": ["dummy"], "repeats_per_receipt": 2},
    }


def test_command_backend_still_works(tmp_path: Path) -> None:
    helper = tmp_path / "helper.py"
    manifest = tmp_path / "manifest.json"
    out_dir = tmp_path / "runs_command"
    cfg_path = tmp_path / "cfg_command.yaml"

    _write_helper(helper)
    _write_manifest(manifest)
    cfg = _base_config(helper, manifest, out_dir)
    cfg["workload"]["backend"] = "command"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    cp = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "PoC2/poc2_operational_runner.py"),
            "--config",
            str(cfg_path),
            "--out-dir",
            str(out_dir),
            "--tag",
            "cmd_backend",
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )

    assert cp.returncode == 0, cp.stderr
    assert (out_dir / "cmd_backend_final_report.json").exists()
    assert not (out_dir / "cmd_backend_gemini_requests.jsonl").exists()


def test_gemini_backend_switch_with_dry_run(tmp_path: Path) -> None:
    helper = tmp_path / "helper.py"
    manifest = tmp_path / "manifest.json"
    out_dir = tmp_path / "runs_gemini"
    cfg_path = tmp_path / "cfg_gemini.yaml"

    _write_helper(helper)
    _write_manifest(manifest)
    cfg = _base_config(helper, manifest, out_dir)
    cfg["workload"] = {
        "backend": "gemini_api",
        "gemini": {
            "model": "gemini-2.5-flash-lite",
            "api_key_env": "GEMINI_API_KEY",
            "timeout_seconds": 5,
            "max_retries": 1,
            "backoff_initial_ms": 1,
            "backoff_max_ms": 5,
            "temperature": 0.0,
            "max_output_tokens": 8,
            "top_p": 1.0,
            "top_k": 1,
            "candidate_count": 1,
            "prompt_template": "INPUT:\n{{input_text}}",
        },
    }
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    cp = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "PoC2/poc2_operational_runner.py"),
            "--config",
            str(cfg_path),
            "--out-dir",
            str(out_dir),
            "--tag",
            "gemini_backend",
            "--dry-run",
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )

    assert cp.returncode == 0, cp.stderr
    assert (out_dir / "gemini_backend_final_report.json").exists()
    assert (out_dir / "gemini_backend_gemini_requests.jsonl").exists()
    assert (out_dir / "gemini_backend_gemini_responses.jsonl").exists()
    assert (out_dir / "gemini_backend_gemini_errors.jsonl").exists()
