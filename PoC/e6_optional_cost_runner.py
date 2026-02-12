
from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import math
import os
import platform
import random
import re
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _strict_object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key in JSON object: {key}")
        out[key] = value
    return out


def _load_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return json.loads(text, object_pairs_hook=_strict_object_pairs_hook)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8", errors="replace"))


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _quantile_sorted(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return sorted_values[low]
    frac = pos - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "n": 0.0,
            "mean": float("nan"),
            "median": float("nan"),
            "p95": float("nan"),
            "min": float("nan"),
            "max": float("nan"),
            "std": float("nan"),
        }
    sorted_values = sorted(values)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {
        "n": float(len(values)),
        "mean": float(statistics.fmean(values)),
        "median": float(statistics.median(values)),
        "p95": float(_quantile_sorted(sorted_values, 0.95)),
        "min": float(sorted_values[0]),
        "max": float(sorted_values[-1]),
        "std": float(std),
    }


def _bootstrap_improvement_ci(
    baseline_totals: list[float],
    pcs_totals: list[float],
    iterations: int,
    seed: int,
) -> list[float]:
    if len(baseline_totals) != len(pcs_totals):
        raise ValueError("bootstrap inputs must have the same length")
    n = len(baseline_totals)
    if n == 0:
        return [float("nan"), float("nan")]
    if n == 1:
        b = baseline_totals[0]
        p = pcs_totals[0]
        val = 1.0 - (p / b) if b > 0 else float("nan")
        return [float(val), float(val)]

    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        idxs = [rng.randrange(n) for _ in range(n)]
        b_mean = statistics.fmean(baseline_totals[i] for i in idxs)
        p_mean = statistics.fmean(pcs_totals[i] for i in idxs)
        if b_mean > 0:
            samples.append(1.0 - (p_mean / b_mean))
    samples.sort()
    return [
        float(_quantile_sorted(samples, 0.025)),
        float(_quantile_sorted(samples, 0.975)),
    ]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _to_nonneg_float(value: Any, context: str) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{context} must be number")
    f = float(value)
    _require(f >= 0.0, f"{context} must be >= 0")
    return f


def _to_pos_int(value: Any, context: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{context} must be integer")
    _require(value > 0, f"{context} must be > 0")
    return value


def _to_nonneg_int(value: Any, context: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{context} must be integer")
    _require(value >= 0, f"{context} must be >= 0")
    return value


def _to_bool(value: Any, context: str) -> bool:
    _require(isinstance(value, bool), f"{context} must be boolean")
    return value


def _tail(text: str, max_chars: int = 500) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _normalize_sha256(value: Any, context: str) -> str:
    _require(_is_nonempty_string(value), f"{context} must be non-empty string")
    raw = str(value).strip()
    if raw.startswith("sha256:"):
        raw = raw.split(":", 1)[1]
    _require(SHA256_HEX_RE.match(raw) is not None, f"{context} must be 64 lowercase hex (or sha256:<hex>)")
    return raw


def _safe_json(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return str(value)
        return value
    if isinstance(value, list):
        return [_safe_json(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in value.items()}
    return str(value)


def _run_quiet(args: list[str], cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


def _probe_git(workspace: Path) -> dict[str, Any]:
    head = None
    dirty = None
    changed_count = None

    rc, stdout, _stderr = _run_quiet(["git", "rev-parse", "HEAD"], workspace)
    if rc == 0:
        head = stdout.strip()

    rc, stdout, _stderr = _run_quiet(["git", "status", "--porcelain"], workspace)
    if rc == 0:
        lines = [line for line in stdout.splitlines() if line.strip()]
        changed_count = len(lines)
        dirty = changed_count > 0

    return {
        "git_head": head,
        "git_is_dirty": dirty,
        "git_changed_count": changed_count,
    }


def _build_environment_fingerprint(workspace: Path) -> dict[str, Any]:
    payload = {
        "generated_utc": _utc_now(),
        "user": getpass.getuser(),
        "hostname": socket.gethostname(),
        "workspace": str(workspace.resolve()).replace("\\", "/"),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }
    payload.update(_probe_git(workspace))
    return payload

def _resolve_command_template(driver: dict[str, Any], phase: str) -> str:
    legacy_key = ""
    template_key = ""
    if phase == "baseline":
        template_key = "baseline_command_template"
        legacy_key = "baseline_command"
    elif phase == "pcs_run":
        template_key = "pcs_run_command_template"
        legacy_key = "pcs_run_command"
    else:
        template_key = "pcs_verify_command_template"
        legacy_key = "pcs_verify_command"

    template = driver.get(template_key)
    if template is None:
        template = driver.get(legacy_key)
    _require(_is_nonempty_string(template), f"driver.{template_key} (or legacy {legacy_key}) is required")
    return str(template)


def _render_command(template: str, context: dict[str, Any]) -> str:
    try:
        return template.format(**context)
    except KeyError as exc:
        raise ValueError(f"command template missing key: {exc}") from exc


def _execute_command(
    command: str,
    cwd: Path,
    timeout_sec: int,
    env_extra: dict[str, str],
) -> dict[str, Any]:
    started_utc = _utc_now()
    start_ns = time.perf_counter_ns()
    env = os.environ.copy()
    env.update(env_extra)

    record: dict[str, Any] = {
        "started_utc": started_utc,
        "command": command,
        "cwd": str(cwd).replace("\\", "/"),
        "timeout_sec": timeout_sec,
    }

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        stdout_text = proc.stdout or ""
        stderr_text = proc.stderr or ""
        record.update(
            {
                "ended_utc": _utc_now(),
                "elapsed_ms": float(elapsed_ms),
                "returncode": int(proc.returncode),
                "status": "ok" if proc.returncode == 0 else "error",
                "stdout_sha256": _sha256_text(stdout_text),
                "stderr_sha256": _sha256_text(stderr_text),
                "stdout_tail": _tail(stdout_text),
                "stderr_tail": _tail(stderr_text),
            }
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        stdout_text = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr_text = exc.stderr if isinstance(exc.stderr, str) else ""
        record.update(
            {
                "ended_utc": _utc_now(),
                "elapsed_ms": float(elapsed_ms),
                "returncode": None,
                "status": "timeout",
                "stdout_sha256": _sha256_text(stdout_text),
                "stderr_sha256": _sha256_text(stderr_text),
                "stdout_tail": _tail(stdout_text),
                "stderr_tail": _tail(stderr_text),
                "error": f"timeout after {timeout_sec}s",
            }
        )

    return record


def _sim_ms(spec: dict[str, Any], rng: random.Random) -> float:
    mean_ms = _to_nonneg_float(spec.get("mean_ms"), "simulation.mean_ms")
    jitter_ms = _to_nonneg_float(spec.get("jitter_ms", 0.0), "simulation.jitter_ms")
    value = mean_ms + rng.uniform(-jitter_ms, jitter_ms)
    return float(max(0.0, value))


def _measure_phase(
    scenario: dict[str, Any],
    phase: str,
    scenario_id: str,
    campaign_idx: int,
    episode_idx: int,
    seed: int,
    rng: random.Random,
    workspace: Path,
    command_log: list[dict[str, Any]],
) -> float:
    mode = scenario["mode"]
    if mode == "simulated":
        sim = scenario["simulation"]
        ms = _sim_ms(sim[phase], rng)
        command_log.append(
            {
                "kind": "simulated",
                "scenario_id": scenario_id,
                "phase": phase,
                "campaign_idx": campaign_idx,
                "episode_idx": episode_idx,
                "seed": seed,
                "elapsed_ms": ms,
                "started_utc": _utc_now(),
                "ended_utc": _utc_now(),
                "status": "ok",
            }
        )
        return ms

    driver = scenario["driver"]
    timeout_sec = _to_pos_int(driver.get("timeout_sec", 120), f"{scenario_id}.driver.timeout_sec")
    cwd_raw = driver.get("cwd")
    cwd = workspace if cwd_raw in (None, "", ".") else (workspace / str(cwd_raw)).resolve()
    _require(cwd.exists(), f"{scenario_id}: driver.cwd does not exist: {cwd}")

    template = _resolve_command_template(driver, phase)
    render_campaign_idx = campaign_idx if campaign_idx >= 0 else 0
    render_episode_idx = episode_idx if episode_idx >= 0 else 0
    context = {
        "scenario_id": scenario_id,
        "campaign_idx": render_campaign_idx,
        "episode_idx": render_episode_idx,
        "seed": seed,
        "phase": phase,
    }
    command = _render_command(template, context)

    env_extra_raw = driver.get("env", {})
    env_extra: dict[str, str] = {}
    if env_extra_raw:
        _require(isinstance(env_extra_raw, dict), f"{scenario_id}.driver.env must be object")
        for key, value in env_extra_raw.items():
            _require(_is_nonempty_string(key), f"{scenario_id}.driver.env key must be non-empty string")
            _require(_is_nonempty_string(value), f"{scenario_id}.driver.env[{key}] must be non-empty string")
            env_extra[str(key)] = str(value)

    record = _execute_command(command, cwd, timeout_sec, env_extra)
    record.update(
        {
            "kind": "command",
            "scenario_id": scenario_id,
            "phase": phase,
            "campaign_idx": campaign_idx,
            "episode_idx": episode_idx,
            "seed": seed,
            "command_template": template,
        }
    )
    command_log.append(record)

    if record["status"] != "ok":
        raise RuntimeError(
            f"command execution failed: scenario={scenario_id}, phase={phase}, campaign={campaign_idx}, "
            f"episode={episode_idx}, status={record['status']}, returncode={record.get('returncode')}"
        )

    return float(record["elapsed_ms"])


def _validate_workload_disclosure(workload: dict[str, Any], prefix: str) -> None:
    _require(isinstance(workload, dict), f"{prefix}.workload must be object")
    _require(_is_nonempty_string(workload.get("workload_id")), f"{prefix}.workload.workload_id is required")
    _require(_is_nonempty_string(workload.get("construction_doc")), f"{prefix}.workload.construction_doc is required")
    _require(_is_nonempty_string(workload.get("harness_version")), f"{prefix}.workload.harness_version is required")
    _normalize_sha256(workload.get("input_manifest_sha256"), f"{prefix}.workload.input_manifest_sha256")


def _validate_command_template_shape(template: str, prefix: str, phase: str) -> None:
    _require("{" in template and "}" in template, f"{prefix}.{phase} template should include placeholders")
    if phase == "baseline" or phase == "pcs_verify":
        _require(
            ("{episode_idx}" in template) or ("{campaign_idx}" in template),
            f"{prefix}.{phase} template should include {{episode_idx}} or {{campaign_idx}} for auditable pairing",
        )
    if phase == "pcs_run":
        _require("{campaign_idx}" in template, f"{prefix}.{phase} template should include {{campaign_idx}}")


def _validate_config(config: dict[str, Any]) -> None:
    _require(isinstance(config, dict), "config root must be object")
    _require(isinstance(config.get("config_version"), str), "config_version must be string")

    min_claim_campaigns = _to_pos_int(config.get("min_claim_campaigns", 30), "min_claim_campaigns")
    min_claim_episodes = _to_pos_int(config.get("min_claim_episodes", 100), "min_claim_episodes")
    _require(min_claim_campaigns >= 10, "min_claim_campaigns should be >= 10")
    _require(min_claim_episodes >= 10, "min_claim_episodes should be >= 10")

    bootstrap_iterations = _to_pos_int(config.get("bootstrap_iterations", 5000), "bootstrap_iterations")
    _require(bootstrap_iterations >= 1000, "bootstrap_iterations should be >= 1000")

    scenarios = config.get("scenarios")
    _require(isinstance(scenarios, list) and len(scenarios) > 0, "scenarios must be non-empty array")

    seen_ids: set[str] = set()
    for idx, scenario in enumerate(scenarios):
        prefix = f"scenarios[{idx}]"
        _require(isinstance(scenario, dict), f"{prefix} must be object")
        _require(_is_nonempty_string(scenario.get("id")), f"{prefix}.id must be non-empty string")
        scenario_id = str(scenario["id"])
        _require(scenario_id not in seen_ids, f"duplicate scenario id: {scenario_id}")
        seen_ids.add(scenario_id)

        _require(scenario.get("mode") in {"simulated", "command"}, f"{prefix}.mode must be simulated or command")
        _to_pos_int(scenario.get("campaigns"), f"{prefix}.campaigns")
        _to_pos_int(scenario.get("episodes_per_campaign"), f"{prefix}.episodes_per_campaign")
        _to_nonneg_int(scenario.get("warmup_runs", 0), f"{prefix}.warmup_runs")

        cost_model = scenario.get("cost_model")
        _require(isinstance(cost_model, dict), f"{prefix}.cost_model must be object")
        _require(_is_nonempty_string(cost_model.get("currency")), f"{prefix}.cost_model.currency must be non-empty string")
        _to_nonneg_float(cost_model.get("cost_per_ms_baseline"), f"{prefix}.cost_model.cost_per_ms_baseline")
        _to_nonneg_float(cost_model.get("cost_per_ms_pcs_run"), f"{prefix}.cost_model.cost_per_ms_pcs_run")
        _to_nonneg_float(cost_model.get("cost_per_ms_pcs_verify"), f"{prefix}.cost_model.cost_per_ms_pcs_verify")
        _to_nonneg_float(cost_model.get("cost_hash_per_verify", 0.0), f"{prefix}.cost_model.cost_hash_per_verify")
        _to_nonneg_float(cost_model.get("cost_registry_per_campaign", 0.0), f"{prefix}.cost_model.cost_registry_per_campaign")
        _to_nonneg_float(cost_model.get("cost_certification_per_campaign", 0.0), f"{prefix}.cost_model.cost_certification_per_campaign")

        if scenario["mode"] == "simulated":
            sim = scenario.get("simulation")
            _require(isinstance(sim, dict), f"{prefix}.simulation must be object")
            for key in ("baseline", "pcs_run", "pcs_verify"):
                _require(isinstance(sim.get(key), dict), f"{prefix}.simulation.{key} must be object")
                _to_nonneg_float(sim[key].get("mean_ms"), f"{prefix}.simulation.{key}.mean_ms")
                _to_nonneg_float(sim[key].get("jitter_ms", 0.0), f"{prefix}.simulation.{key}.jitter_ms")
        else:
            workload = scenario.get("workload")
            _validate_workload_disclosure(workload, prefix)

            driver = scenario.get("driver")
            _require(isinstance(driver, dict), f"{prefix}.driver must be object")
            _to_pos_int(driver.get("timeout_sec", 120), f"{prefix}.driver.timeout_sec")
            if "randomize_episode_order" in driver:
                _to_bool(driver["randomize_episode_order"], f"{prefix}.driver.randomize_episode_order")

            baseline_template = _resolve_command_template(driver, "baseline")
            pcs_run_template = _resolve_command_template(driver, "pcs_run")
            pcs_verify_template = _resolve_command_template(driver, "pcs_verify")
            _validate_command_template_shape(baseline_template, f"{prefix}.driver", "baseline")
            _validate_command_template_shape(pcs_run_template, f"{prefix}.driver", "pcs_run")
            _validate_command_template_shape(pcs_verify_template, f"{prefix}.driver", "pcs_verify")

def _write_markdown(report: dict[str, Any], out_path: Path) -> None:
    lines: list[str] = []
    lines.append("# E6 Optional Cost/Latency Report")
    lines.append("")
    lines.append(f"- generated_utc: `{report['generated_utc']}`")
    lines.append(f"- config_sha256: `{report['config_sha256']}`")
    lines.append(f"- python_version: `{report['environment']['python_version']}`")
    lines.append("")
    lines.append("## Claim Guardrails")
    for item in report["claim_guardrails"]:
        lines.append(f"- {item}")
    lines.append("")

    failed_ids = report.get("failed_scenarios", [])
    lines.append(f"- failed_scenarios: `{failed_ids}`")
    lines.append("")

    for scenario in report["scenarios"]:
        agg = scenario.get("aggregate", {})
        lines.append(f"## Scenario `{scenario['id']}`")
        lines.append("")
        lines.append(f"- mode: `{scenario['mode']}`")
        lines.append(f"- status: `{scenario['status']}`")
        lines.append(f"- campaigns_completed: `{scenario['campaigns_completed']}` / `{scenario['campaigns_planned']}`")
        lines.append(f"- episodes_per_campaign: `{scenario['episodes_per_campaign']}`")
        lines.append(f"- currency: `{scenario['currency']}`")
        lines.append(f"- supports_operational_claim: `{str(scenario['supports_operational_claim']).lower()}`")
        lines.append("")

        if scenario.get("error"):
            lines.append(f"- error: `{scenario['error']}`")
            lines.append("")
            continue

        lines.append("| metric | mean | median | p95 |")
        lines.append("|---|---:|---:|---:|")
        lines.append(
            f"| baseline_total_ms | {agg['baseline_total_ms']['mean']:.3f} | {agg['baseline_total_ms']['median']:.3f} | {agg['baseline_total_ms']['p95']:.3f} |"
        )
        lines.append(f"| pcs_total_ms | {agg['pcs_total_ms']['mean']:.3f} | {agg['pcs_total_ms']['median']:.3f} | {agg['pcs_total_ms']['p95']:.3f} |")
        lines.append(
            f"| latency_improvement_ratio | {agg['latency_improvement_ratio']['mean']:.6f} | {agg['latency_improvement_ratio']['median']:.6f} | {agg['latency_improvement_ratio']['p95']:.6f} |"
        )
        lines.append(
            f"| baseline_cost_total | {agg['baseline_cost_total']['mean']:.6f} | {agg['baseline_cost_total']['median']:.6f} | {agg['baseline_cost_total']['p95']:.6f} |"
        )
        lines.append(f"| pcs_cost_total | {agg['pcs_cost_total']['mean']:.6f} | {agg['pcs_cost_total']['median']:.6f} | {agg['pcs_cost_total']['p95']:.6f} |")
        lines.append(
            f"| cost_improvement_ratio | {agg['cost_improvement_ratio']['mean']:.6f} | {agg['cost_improvement_ratio']['median']:.6f} | {agg['cost_improvement_ratio']['p95']:.6f} |"
        )
        lines.append("")
        lines.append(f"- latency_improvement_ratio_ci95: `{agg['latency_improvement_ratio_ci95']}`")
        lines.append(f"- cost_improvement_ratio_ci95: `{agg['cost_improvement_ratio_ci95']}`")
        lines.append(f"- cost_benefit_inequality_mean_holds: `{str(agg['cost_benefit_inequality_mean_holds']).lower()}`")
        lines.append(f"- latency_benefit_inequality_mean_holds: `{str(agg['latency_benefit_inequality_mean_holds']).lower()}`")
        lines.append("")
        lines.append("### Claim Criteria")
        for key, value in scenario["claim_criteria"].items():
            lines.append(f"- {key}: `{str(value).lower() if isinstance(value, bool) else value}`")
        lines.append("")
        for note in scenario["interpretation_notes"]:
            lines.append(f"- {note}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="E6 optional external cost/latency experiment runner")
    parser.add_argument("--config", required=True, help="Path to JSON config")
    parser.add_argument("--out-dir", default="PoC/runs", help="Output directory")
    parser.add_argument("--tag", default="e6_optional_cost", help="Output file prefix")
    args = parser.parse_args()

    workspace = Path.cwd()
    config_path = Path(args.config)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = _load_json(config_path)
    _validate_config(config)

    master_seed = int(config.get("seed", 20260212))
    bootstrap_iterations = int(config.get("bootstrap_iterations", 5000))
    min_claim_campaigns = int(config.get("min_claim_campaigns", 30))
    min_claim_episodes = int(config.get("min_claim_episodes", 100))

    environment = _build_environment_fingerprint(workspace)

    enabled_scenarios = [s for s in config["scenarios"] if s.get("enabled", True)]
    if not enabled_scenarios:
        raise ValueError("no enabled scenarios in config")

    csv_rows: list[dict[str, Any]] = []
    command_log: list[dict[str, Any]] = []
    scenario_reports: list[dict[str, Any]] = []
    failed_scenarios: list[str] = []

    for s_idx, scenario in enumerate(enabled_scenarios):
        scenario_id = str(scenario["id"])
        campaigns = _to_pos_int(scenario["campaigns"], f"{scenario_id}.campaigns")
        episodes = _to_pos_int(scenario["episodes_per_campaign"], f"{scenario_id}.episodes_per_campaign")
        warmup_runs = _to_nonneg_int(scenario.get("warmup_runs", 0), f"{scenario_id}.warmup_runs")

        rng = random.Random(master_seed + (s_idx * 9973))
        baseline_totals: list[float] = []
        pcs_totals: list[float] = []
        baseline_cost_totals: list[float] = []
        pcs_cost_totals: list[float] = []
        latency_improvements: list[float] = []
        cost_improvements: list[float] = []

        scenario_error = None
        completed_campaigns = 0

        try:
            for warmup_idx in range(warmup_runs):
                warmup_seed = master_seed + (s_idx * 1_000_000) + warmup_idx
                _measure_phase(scenario, "baseline", scenario_id, -1, -1, warmup_seed, rng, workspace, command_log)
                _measure_phase(scenario, "pcs_run", scenario_id, -1, -1, warmup_seed, rng, workspace, command_log)
                _measure_phase(scenario, "pcs_verify", scenario_id, -1, -1, warmup_seed, rng, workspace, command_log)

            cost_model = scenario["cost_model"]
            currency = str(cost_model["currency"])
            cost_per_ms_baseline = _to_nonneg_float(cost_model["cost_per_ms_baseline"], f"{scenario_id}.cost_per_ms_baseline")
            cost_per_ms_run = _to_nonneg_float(cost_model["cost_per_ms_pcs_run"], f"{scenario_id}.cost_per_ms_pcs_run")
            cost_per_ms_verify = _to_nonneg_float(cost_model["cost_per_ms_pcs_verify"], f"{scenario_id}.cost_per_ms_pcs_verify")
            cost_hash_per_verify = _to_nonneg_float(cost_model.get("cost_hash_per_verify", 0.0), f"{scenario_id}.cost_hash_per_verify")
            cost_registry = _to_nonneg_float(cost_model.get("cost_registry_per_campaign", 0.0), f"{scenario_id}.cost_registry_per_campaign")
            cost_cert = _to_nonneg_float(cost_model.get("cost_certification_per_campaign", 0.0), f"{scenario_id}.cost_certification_per_campaign")

            randomize_order = False
            if scenario["mode"] == "command":
                randomize_order = bool(scenario.get("driver", {}).get("randomize_episode_order", True))

            for campaign_idx in range(campaigns):
                baseline_total_ms = 0.0
                pcs_verify_total_ms = 0.0
                campaign_seed = master_seed + (s_idx * 1_000_000) + campaign_idx

                pcs_run_ms = _measure_phase(
                    scenario,
                    "pcs_run",
                    scenario_id,
                    campaign_idx,
                    -1,
                    campaign_seed,
                    rng,
                    workspace,
                    command_log,
                )

                for episode_idx in range(episodes):
                    phases = ["baseline", "pcs_verify"]
                    if randomize_order:
                        rng.shuffle(phases)
                    for phase in phases:
                        elapsed = _measure_phase(
                            scenario,
                            phase,
                            scenario_id,
                            campaign_idx,
                            episode_idx,
                            campaign_seed,
                            rng,
                            workspace,
                            command_log,
                        )
                        if phase == "baseline":
                            baseline_total_ms += elapsed
                        else:
                            pcs_verify_total_ms += elapsed

                pcs_total_ms = pcs_run_ms + pcs_verify_total_ms

                baseline_cost_total = baseline_total_ms * cost_per_ms_baseline
                pcs_cost_run = pcs_run_ms * cost_per_ms_run
                pcs_cost_check = pcs_verify_total_ms * cost_per_ms_verify
                pcs_cost_hash = cost_hash_per_verify * episodes
                pcs_cost_total = pcs_cost_run + pcs_cost_check + pcs_cost_hash + cost_registry + cost_cert

                _require(baseline_total_ms > 0.0, f"{scenario_id}: baseline_total_ms must be > 0")
                _require(baseline_cost_total > 0.0, f"{scenario_id}: baseline_cost_total must be > 0")

                latency_improvement = 1.0 - (pcs_total_ms / baseline_total_ms)
                cost_improvement = 1.0 - (pcs_cost_total / baseline_cost_total)

                baseline_totals.append(float(baseline_total_ms))
                pcs_totals.append(float(pcs_total_ms))
                baseline_cost_totals.append(float(baseline_cost_total))
                pcs_cost_totals.append(float(pcs_cost_total))
                latency_improvements.append(float(latency_improvement))
                cost_improvements.append(float(cost_improvement))

                csv_rows.append(
                    {
                        "scenario_id": scenario_id,
                        "scenario_mode": scenario["mode"],
                        "campaign_index": campaign_idx,
                        "episodes_per_campaign": episodes,
                        "baseline_total_ms": baseline_total_ms,
                        "pcs_run_ms": pcs_run_ms,
                        "pcs_verify_total_ms": pcs_verify_total_ms,
                        "pcs_total_ms": pcs_total_ms,
                        "latency_improvement_ratio": latency_improvement,
                        "baseline_cost_total": baseline_cost_total,
                        "pcs_cost_total": pcs_cost_total,
                        "cost_improvement_ratio": cost_improvement,
                        "pcs_cost_run": pcs_cost_run,
                        "pcs_cost_check": pcs_cost_check,
                        "pcs_cost_hash": pcs_cost_hash,
                        "pcs_cost_registry": cost_registry,
                        "pcs_cost_certification": cost_cert,
                        "amortized_cost_cert_per_episode": (cost_cert / episodes),
                        "cost_benefit_holds_campaign": bool(pcs_cost_total < baseline_cost_total),
                        "latency_benefit_holds_campaign": bool(pcs_total_ms < baseline_total_ms),
                    }
                )
                completed_campaigns += 1

        except Exception as exc:
            scenario_error = str(exc)
            failed_scenarios.append(scenario_id)

        currency = str(scenario["cost_model"]["currency"])

        if completed_campaigns > 0:
            latency_ci = _bootstrap_improvement_ci(
                baseline_totals=baseline_totals,
                pcs_totals=pcs_totals,
                iterations=bootstrap_iterations,
                seed=master_seed + (s_idx * 1297) + 11,
            )
            cost_ci = _bootstrap_improvement_ci(
                baseline_totals=baseline_cost_totals,
                pcs_totals=pcs_cost_totals,
                iterations=bootstrap_iterations,
                seed=master_seed + (s_idx * 1297) + 97,
            )
        else:
            latency_ci = [float("nan"), float("nan")]
            cost_ci = [float("nan"), float("nan")]

        workload_disclosure_complete = False
        if scenario["mode"] == "command":
            workload = scenario.get("workload", {})
            workload_disclosure_complete = (
                isinstance(workload, dict)
                and _is_nonempty_string(workload.get("workload_id"))
                and _is_nonempty_string(workload.get("construction_doc"))
                and _is_nonempty_string(workload.get("harness_version"))
                and SHA256_HEX_RE.match(str(workload.get("input_manifest_sha256", "")).replace("sha256:", "")) is not None
            )

        criteria = {
            "mode_is_command": scenario["mode"] == "command",
            "workload_disclosure_complete": workload_disclosure_complete,
            "scenario_status_ok": scenario_error is None,
            "minimum_campaigns_met": completed_campaigns >= min_claim_campaigns,
            "minimum_episodes_met": episodes >= min_claim_episodes,
            "latency_ci_lower_positive": bool(math.isfinite(latency_ci[0]) and latency_ci[0] > 0.0),
            "cost_ci_lower_positive": bool(math.isfinite(cost_ci[0]) and cost_ci[0] > 0.0),
        }
        supports_claim = all(criteria.values())

        notes = [
            "Cost model used: CostPCS = Costrun + Costcheck + Costhash + Costregistry + amortized Costcert.",
            "Positive improvement means PCS total is lower than baseline total.",
            "Operational claim readiness requires command mode, disclosure completeness, sufficient sample size, and positive CI lower bounds.",
        ]
        if scenario["mode"] == "simulated":
            notes.append("This scenario is simulated and cannot support real operational claims.")

        scenario_report: dict[str, Any] = {
            "id": scenario_id,
            "mode": scenario["mode"],
            "status": "ok" if scenario_error is None else "failed",
            "error": scenario_error,
            "campaigns_planned": campaigns,
            "campaigns_completed": completed_campaigns,
            "episodes_per_campaign": episodes,
            "currency": currency,
            "supports_operational_claim": supports_claim,
            "claim_criteria": criteria,
            "interpretation_notes": notes,
        }

        if scenario_error is None and completed_campaigns > 0:
            scenario_report["aggregate"] = {
                "baseline_total_ms": _summary(baseline_totals),
                "pcs_total_ms": _summary(pcs_totals),
                "latency_improvement_ratio": _summary(latency_improvements),
                "latency_improvement_ratio_ci95": latency_ci,
                "baseline_cost_total": _summary(baseline_cost_totals),
                "pcs_cost_total": _summary(pcs_cost_totals),
                "cost_improvement_ratio": _summary(cost_improvements),
                "cost_improvement_ratio_ci95": cost_ci,
                "cost_benefit_inequality_mean_holds": bool(statistics.fmean(pcs_cost_totals) < statistics.fmean(baseline_cost_totals)),
                "latency_benefit_inequality_mean_holds": bool(statistics.fmean(pcs_totals) < statistics.fmean(baseline_totals)),
            }

        if scenario["mode"] == "command":
            scenario_report["workload"] = _safe_json(scenario.get("workload", {}))

        scenario_reports.append(scenario_report)

    report = {
        "generated_utc": _utc_now(),
        "tool": "PoC/e6_optional_cost_runner.py",
        "config_path": str(config_path).replace("\\", "/"),
        "config_sha256": _sha256_file(config_path),
        "bootstrap_iterations": bootstrap_iterations,
        "seed": master_seed,
        "min_claim_campaigns": min_claim_campaigns,
        "min_claim_episodes": min_claim_episodes,
        "paper_alignment": {
            "cost_equation_core": "CostPCS = Costrun + Costcheck + Costhash + Costregistry",
            "amortization_term": "Costcert / E[Nreuse]",
            "benefit_condition": "E[CostPCS] + Costcert/E[Nreuse] < E[Costfull]",
        },
        "claim_guardrails": [
            "E6 is optional and non-gating for PCS-Core verifier correctness claims.",
            "Do not report simulated scenarios as operational evidence.",
            "Report all executed runs, including null or negative outcomes.",
            "Publish workload construction and command templates for reproducibility.",
            "Keep measured facts separate from interpretation and policy claims.",
        ],
        "environment": environment,
        "failed_scenarios": failed_scenarios,
        "scenarios": scenario_reports,
    }

    csv_path = out_dir / f"{args.tag}_campaigns.csv"
    json_path = out_dir / f"{args.tag}_report.json"
    md_path = out_dir / f"{args.tag}_report.md"
    config_copy_path = out_dir / f"{args.tag}_effective_config.json"
    command_log_path = out_dir / f"{args.tag}_command_log.jsonl"
    env_path = out_dir / f"{args.tag}_environment_fingerprint.json"

    fieldnames = [
        "scenario_id",
        "scenario_mode",
        "campaign_index",
        "episodes_per_campaign",
        "baseline_total_ms",
        "pcs_run_ms",
        "pcs_verify_total_ms",
        "pcs_total_ms",
        "latency_improvement_ratio",
        "baseline_cost_total",
        "pcs_cost_total",
        "cost_improvement_ratio",
        "pcs_cost_run",
        "pcs_cost_check",
        "pcs_cost_hash",
        "pcs_cost_registry",
        "pcs_cost_certification",
        "amortized_cost_cert_per_episode",
        "cost_benefit_holds_campaign",
        "latency_benefit_holds_campaign",
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in csv_rows:
            writer.writerow(row)

    with command_log_path.open("w", encoding="utf-8", newline="") as f:
        for row in command_log:
            f.write(json.dumps(_safe_json(row), ensure_ascii=False, sort_keys=True) + "\n")

    json_path.write_text(json.dumps(_safe_json(report), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _write_markdown(report, md_path)
    config_copy_path.write_text(json.dumps(_safe_json(config), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    env_path.write_text(json.dumps(_safe_json(environment), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(
        json.dumps(
            {
                "csv": str(csv_path),
                "json": str(json_path),
                "md": str(md_path),
                "config_copy": str(config_copy_path),
                "command_log": str(command_log_path),
                "environment": str(env_path),
                "failed_scenarios": failed_scenarios,
            },
            ensure_ascii=False,
        )
    )
    return 2 if failed_scenarios else 0


if __name__ == "__main__":
    raise SystemExit(main())
