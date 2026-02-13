#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"YAML root must be object: {path}")
    return obj


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def count_enabled_complexities(cfg: dict[str, Any]) -> int:
    profiles = cfg.get("workloads", {}).get("complexity_profiles", {})
    if not isinstance(profiles, dict):
        return 0
    return sum(1 for _, v in profiles.items() if isinstance(v, dict) and v.get("enabled", True))


def count_enabled_workloads(cfg: dict[str, Any]) -> int:
    families = cfg.get("workloads", {}).get("families", [])
    if not isinstance(families, list):
        return 0
    return sum(1 for w in families if isinstance(w, dict) and w.get("enabled", True))


def estimate_cells(cfg: dict[str, Any]) -> int:
    w = count_enabled_workloads(cfg)
    c = count_enabled_complexities(cfg)
    exp = cfg.get("experiment", {})
    cost = cfg.get("cost_model", {})
    conc = len(exp.get("concurrency_levels", []) or [])
    reuse = len(exp.get("reuse_counts_N", []) or [])
    scen = len(cost.get("scenarios", []) or [])
    fixed = len(cost.get("fixed_overheads", []) or [])
    cells = w * c * conc * reuse * scen * fixed
    max_cells = int(exp.get("max_cells", 0) or 0)
    return min(cells, max_cells) if max_cells > 0 else cells


def estimate_campaign_rows(cfg: dict[str, Any]) -> int:
    exp = cfg.get("experiment", {})
    reps = int(exp.get("campaign_repetitions_per_cell", 0) or 0)
    return estimate_cells(cfg) * reps


def estimate_api_calls(cfg: dict[str, Any]) -> int:
    exp = cfg.get("experiment", {})
    rows = estimate_campaign_rows(cfg)
    reuse_counts = exp.get("reuse_counts_N", []) or [1]
    warmup = int(exp.get("warmup_episodes", 0) or 0)
    n_mean = sum(int(x) for x in reuse_counts) / max(1, len(reuse_counts))
    calls_per_campaign = (2 * n_mean) + 1 + (warmup * 3)
    return int(round(rows * calls_per_campaign))


def stage_iter(profiles: dict[str, Any], only_stage: str | None) -> list[dict[str, Any]]:
    stages = profiles.get("stages")
    if not isinstance(stages, list):
        raise ValueError("profiles.stages must be list")
    out: list[dict[str, Any]] = []
    for s in stages:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id", "")).strip()
        if not sid:
            continue
        if only_stage and sid != only_stage:
            continue
        out.append(s)
    if only_stage and not out:
        raise ValueError(f"stage not found: {only_stage}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate staged PoC2 configs from base config and stage profiles.")
    parser.add_argument("--base-config", default="PoC2/poc2_operational_config.yaml")
    parser.add_argument("--profiles", default="PoC2/poc2_stage_profiles.yaml")
    parser.add_argument("--out-dir", default="PoC2/staged-configs")
    parser.add_argument("--stage", default="", help="optional stage id (e.g., S1_PILOT)")
    args = parser.parse_args()

    base_cfg_path = Path(args.base_config).resolve()
    profile_path = Path(args.profiles).resolve()
    out_dir = Path(args.out_dir).resolve()
    base_cfg_ref = args.base_config
    profile_ref = args.profiles

    base_cfg = load_yaml(base_cfg_path)
    profiles = load_yaml(profile_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    selected = stage_iter(profiles, args.stage.strip() or None)
    summary: list[dict[str, Any]] = []

    for stage in selected:
        sid = str(stage["id"])
        tag = str(stage.get("tag", sid.lower()))
        overrides = stage.get("overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError(f"overrides must be object for stage: {sid}")

        cfg = deep_merge(base_cfg, overrides)
        cfg["artifact_prefix"] = tag
        cfg["_stage_meta"] = {
            "stage_id": sid,
            "source_base_config": base_cfg_ref,
            "source_profiles": profile_ref,
        }

        out_path = out_dir / f"poc2_{sid.lower()}.yaml"
        out_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")

        summary.append(
            {
                "stage_id": sid,
                "tag": tag,
                "config_path": str(out_path),
                "estimated_cells": estimate_cells(cfg),
                "estimated_campaign_rows": estimate_campaign_rows(cfg),
                "estimated_api_calls": estimate_api_calls(cfg),
            }
        )

    print(json.dumps({"generated": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
