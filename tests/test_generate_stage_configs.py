from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_generate_stage_configs(tmp_path: Path) -> None:
    base = {
        "artifact_prefix": "poc2",
        "workloads": {
            "families": [{"id": "W1", "enabled": True}],
            "complexity_profiles": {"light": {"enabled": True}, "heavy": {"enabled": False}},
        },
        "experiment": {
            "concurrency_levels": [1],
            "reuse_counts_N": [1, 3],
            "campaign_repetitions_per_cell": 2,
            "warmup_episodes": 0,
            "max_cells": 0,
        },
        "cost_model": {"scenarios": [{"id": "C1"}], "fixed_overheads": [{"id": "F1"}]},
    }
    profiles = {
        "stages": [
            {
                "id": "S_TEST",
                "tag": "poc2_s_test",
                "overrides": {
                    "experiment": {"campaign_repetitions_per_cell": 1, "max_cells": 1},
                    "workloads": {"complexity_profiles": {"heavy": {"enabled": True}}},
                },
            }
        ]
    }

    base_path = tmp_path / "base.yaml"
    prof_path = tmp_path / "profiles.yaml"
    out_dir = tmp_path / "out"
    base_path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    prof_path.write_text(yaml.safe_dump(profiles, sort_keys=False), encoding="utf-8")

    cp = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "PoC2/generate_stage_configs.py"),
            "--base-config",
            str(base_path),
            "--profiles",
            str(prof_path),
            "--out-dir",
            str(out_dir),
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )
    assert cp.returncode == 0, cp.stderr

    out_json = json.loads(cp.stdout)
    assert isinstance(out_json.get("generated"), list)
    assert len(out_json["generated"]) == 1
    assert out_json["generated"][0]["stage_id"] == "S_TEST"
    assert out_json["generated"][0]["estimated_cells"] == 1

    gen_cfg = out_dir / "poc2_s_test.yaml"
    assert gen_cfg.exists()
    cfg_obj = yaml.safe_load(gen_cfg.read_text(encoding="utf-8"))
    assert cfg_obj["artifact_prefix"] == "poc2_s_test"
    assert cfg_obj["_stage_meta"]["stage_id"] == "S_TEST"
