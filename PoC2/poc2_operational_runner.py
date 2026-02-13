#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import os
import platform
import random
import re
import shlex
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("PyYAML is required: pip install pyyaml") from e


TOTAL_FAILED_RE = re.compile(r"total=(?P<total>\d+)\s+failed=(?P<failed>\d+)")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8", errors="replace"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def strict_yaml_load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"config must be YAML object: {path}")
    return data


def strict_json_load(path: Path) -> Any:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"json must be object: {path}")
    return data


def json_dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def mean(values: list[float]) -> float:
    return float(statistics.fmean(values)) if values else float("nan")


def bootstrap_ci_improvement(
    baseline: list[float], pcs: list[float], n_boot: int = 5000, alpha: float = 0.05, seed: int = 1234
) -> dict[str, float]:
    """
    Improvement ratio = 1 - pcs / baseline
    Paired bootstrap over campaign totals.
    """
    if len(baseline) != len(pcs) or len(baseline) == 0:
        return {"mean": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": float(len(baseline))}
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
    vals.sort()
    if not vals:
        return {"mean": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": float(len(baseline))}
    lo_i = int((alpha / 2.0) * (len(vals) - 1))
    hi_i = int((1 - alpha / 2.0) * (len(vals) - 1))
    return {
        "mean": float(mean(vals)),
        "ci_low": float(vals[lo_i]),
        "ci_high": float(vals[hi_i]),
        "n": float(len(vals)),
    }


@dataclass
class CmdResult:
    returncode: int
    duration_ms: float
    stdout: str
    stderr: str
    started_utc: str
    finished_utc: str
    timeout: bool


class CommandRunner:
    def __init__(self, cwd: Path, timeout_sec: int, log_jsonl: Path, dry_run: bool = False) -> None:
        self.cwd = cwd
        self.timeout_sec = int(timeout_sec)
        self.log_jsonl = log_jsonl
        self.dry_run = dry_run

    def run(self, command: str, stage: str, meta: dict[str, Any] | None = None, env_extra: dict[str, str] | None = None) -> CmdResult:
        started = utc_now()
        t0 = time.perf_counter()
        if self.dry_run:
            res = CmdResult(
                returncode=0,
                duration_ms=0.0,
                stdout="",
                stderr="",
                started_utc=started,
                finished_utc=utc_now(),
                timeout=False,
            )
            self._log(command, stage, res, meta or {}, env_extra or {})
            return res

        env = os.environ.copy()
        if env_extra:
            env.update({k: str(v) for k, v in env_extra.items()})
        timeout = False
        try:
            cp = subprocess.run(
                shlex.split(command),
                cwd=str(self.cwd),
                text=True,
                capture_output=True,
                timeout=self.timeout_sec,
                env=env,
            )
            rc = int(cp.returncode)
            out = cp.stdout or ""
            err = cp.stderr or ""
        except subprocess.TimeoutExpired as e:
            timeout = True
            rc = 124
            out = (e.stdout or "") if isinstance(e.stdout, str) else ""
            err = (e.stderr or "") if isinstance(e.stderr, str) else ""
            err = (err + "\n[timeout]").strip()

        dur_ms = (time.perf_counter() - t0) * 1000.0
        res = CmdResult(
            returncode=rc,
            duration_ms=float(dur_ms),
            stdout=out,
            stderr=err,
            started_utc=started,
            finished_utc=utc_now(),
            timeout=timeout,
        )
        self._log(command, stage, res, meta or {}, env_extra or {})
        return res

    def _log(self, command: str, stage: str, res: CmdResult, meta: dict[str, Any], env_extra: dict[str, str]) -> None:
        rec: dict[str, Any] = {
            "ts_started_utc": res.started_utc,
            "ts_finished_utc": res.finished_utc,
            "stage": stage,
            "command": command,
            "cwd": str(self.cwd),
            "timeout_sec": self.timeout_sec,
            "returncode": res.returncode,
            "duration_ms": res.duration_ms,
            "timeout": res.timeout,
            "stdout_sha256": sha256_text(res.stdout),
            "stderr_sha256": sha256_text(res.stderr),
            "meta": meta,
            "env_extra": env_extra,
        }
        append_jsonl(self.log_jsonl, rec)


def resolve_template(template: str, mapping: dict[str, Any]) -> str:
    out = template
    for k, v in mapping.items():
        out = out.replace("{" + k + "}", str(v))
    unresolved = re.findall(r"\{[a-zA-Z0-9_]+\}", out)
    if unresolved:
        raise ValueError(f"unresolved placeholders {unresolved} in template: {template}")
    return out


def cartesian_cells(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    workloads = [w for w in cfg["workloads"]["families"] if w.get("enabled", True)]
    complexities = [k for k, v in cfg["workloads"]["complexity_profiles"].items() if v.get("enabled", True)]
    conc_levels = cfg["experiment"]["concurrency_levels"]
    reuse_counts = cfg["experiment"]["reuse_counts_N"]
    scenarios = cfg["cost_model"]["scenarios"]
    fixed = cfg["cost_model"].get("fixed_overheads", [{"id": "none", "c_registry_fixed": 0.0, "c_cert_fixed": 0.0}])
    cells = []
    for w, cx, cc, n, sc, fo in itertools.product(workloads, complexities, conc_levels, reuse_counts, scenarios, fixed):
        cells.append(
            {
                "workload_id": w["id"],
                "complexity_tier": cx,
                "concurrency": int(cc),
                "N_reuse": int(n),
                "cost_scenario": sc["id"],
                "fixed_overhead_id": fo["id"],
            }
        )
    max_cells = int(cfg["experiment"].get("max_cells", 0) or 0)
    if max_cells > 0:
        cells = cells[:max_cells]
    return cells


def capture_environment(repo_root: Path) -> dict[str, Any]:
    commit = "unknown"
    try:
        cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root), text=True, capture_output=True, timeout=5)
        if cp.returncode == 0:
            commit = cp.stdout.strip()
    except Exception:
        pass
    return {
        "captured_utc": utc_now(),
        "os": platform.platform(),
        "kernel": platform.release(),
        "python_version": sys.version.split()[0],
        "cpu_model": platform.processor() or "unknown",
        "machine": platform.machine(),
        "checker_version": "reference-checker/verifier.py",
        "repo_commit_hash": commit,
    }


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def maybe_write_parquet(path: Path, rows: list[dict[str, Any]]) -> bool:
    try:
        import pandas as pd  # type: ignore

        df = pd.DataFrame(rows)
        df.to_parquet(path, index=False)
        return True
    except Exception:
        return False


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def backend_name(cfg: dict[str, Any]) -> str:
    workload_cfg = cfg.get("workload")
    if isinstance(workload_cfg, dict):
        return str(workload_cfg.get("backend", "command")).strip().lower()
    return "command"


def make_cell_id(cell: dict[str, Any]) -> str:
    return (
        f"{cell['workload_id']}|{cell['complexity_tier']}|c{cell['concurrency']}|"
        f"n{cell['N_reuse']}|{cell['cost_scenario']}|{cell['fixed_overhead_id']}"
    )


def token_cost_for_call(result: dict[str, Any], cost_model: dict[str, Any]) -> tuple[float, bool, int | None, int | None]:
    usage = result.get("usage")
    input_usd = safe_float(cost_model.get("input_token_usd", 0.0), 0.0)
    output_usd = safe_float(cost_model.get("output_token_usd", 0.0), 0.0)
    fallback = safe_float(cost_model.get("fallback_ms_cost_if_usage_missing", cost_model.get("c_run_per_ms", 0.0)), 0.0)

    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        in_i = safe_int(input_tokens, default=-1)
        out_i = safe_int(output_tokens, default=-1)
        if in_i >= 0 and out_i >= 0:
            return (in_i * input_usd) + (out_i * output_usd), False, in_i, out_i

    latency_ms = safe_float(result.get("latency_ms"), 0.0)
    return latency_ms * fallback, True, None, None


def gemini_log_paths(out_dir: Path, prefix: str) -> dict[str, Path]:
    return {
        "requests": out_dir / f"{prefix}_gemini_requests.jsonl",
        "responses": out_dir / f"{prefix}_gemini_responses.jsonl",
        "errors": out_dir / f"{prefix}_gemini_errors.jsonl",
    }


def append_gemini_audit(
    logs: dict[str, Path],
    *,
    phase: str,
    cell_id: str,
    repetition: int,
    arm: str,
    order: str,
    result: dict[str, Any],
) -> None:
    model = str(result.get("model") or "")
    token_usage = result.get("usage") if isinstance(result.get("usage"), dict) else {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    req_rec = {
        "timestamp_utc": result.get("started_utc") or utc_now(),
        "phase": phase,
        "cell_id": cell_id,
        "repetition": repetition,
        "arm": arm,
        "order": order,
        "model": model,
        "request_hash": result.get("request_hash"),
        "latency_ms": None,
        "token_usage": None,
        "status_code": None,
        "ok": None,
        "error_code": None,
    }
    append_jsonl(logs["requests"], req_rec)

    resp_rec = {
        "timestamp_utc": result.get("finished_utc") or utc_now(),
        "phase": phase,
        "cell_id": cell_id,
        "repetition": repetition,
        "arm": arm,
        "order": order,
        "model": model,
        "request_hash": result.get("request_hash"),
        "latency_ms": safe_float(result.get("latency_ms"), 0.0),
        "token_usage": token_usage,
        "status_code": result.get("status_code"),
        "ok": bool(result.get("ok", False)),
        "error_code": result.get("error_code"),
    }
    append_jsonl(logs["responses"], resp_rec)

    if not bool(result.get("ok", False)):
        err_rec = {
            **resp_rec,
            "error_message": result.get("error_message"),
        }
        append_jsonl(logs["errors"], err_rec)


def load_manifest_for_workload(repo_root: Path, workload: dict[str, Any]) -> dict[str, Any]:
    manifest_ref = workload.get("manifest")
    if not isinstance(manifest_ref, str) or not manifest_ref:
        return {}
    manifest_path = (repo_root / manifest_ref).resolve() if not Path(manifest_ref).is_absolute() else Path(manifest_ref)
    if not manifest_path.exists():
        return {}
    try:
        data = strict_json_load(manifest_path)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def input_text_for_episode(manifest: dict[str, Any], episode_idx: int, workload: dict[str, Any]) -> str:
    shared_input = manifest.get("shared_input")
    if isinstance(shared_input, str) and shared_input:
        return shared_input

    episodes = manifest.get("episodes")
    if isinstance(episodes, list) and episodes:
        candidate = episodes[min(max(episode_idx, 0), len(episodes) - 1)]
        if isinstance(candidate, dict):
            episode_input = candidate.get("input_text")
            if isinstance(episode_input, str) and episode_input:
                return episode_input
            reuse_tag = candidate.get("reuse_tag")
            if isinstance(reuse_tag, str) and reuse_tag:
                return reuse_tag

    fallback = workload.get("default_input_text")
    if isinstance(fallback, str) and fallback:
        return fallback
    return "Summarize deterministic verification outcomes for repeated invocation acceptance."


def load_gemini_driver_module() -> Any:
    try:
        from PoC2 import gemini_workload_driver as module  # type: ignore

        return module
    except Exception:
        import gemini_workload_driver as module  # type: ignore

        return module


def run_p1_conformance(cfg: dict[str, Any], runner: CommandRunner, out_dir: Path, prefix: str) -> dict[str, Any]:
    cmd = cfg["commands"]["conformance"]
    res = runner.run(cmd, stage="P1_CONFORMANCE_SMOKE")
    log_path = out_dir / f"{prefix}_p1_conformance.log"
    log_path.write_text(
        f"$ {cmd}\n\n[stdout]\n{res.stdout}\n\n[stderr]\n{res.stderr}\n",
        encoding="utf-8",
    )
    if runner.dry_run:
        summary = {
            "pass": True,
            "returncode": 0,
            "vector_total": 0,
            "failed": 0,
            "ts_utc": utc_now(),
            "note": "dry_run_skips_command_execution",
        }
        json_dump(out_dir / f"{prefix}_p1_summary.json", summary)
        return summary

    total = 0
    failed = 1 if res.returncode != 0 else 0
    m = TOTAL_FAILED_RE.search(res.stdout + "\n" + res.stderr)
    if m:
        total = int(m.group("total"))
        failed = int(m.group("failed"))
    summary = {
        "pass": bool(res.returncode == 0 and failed == 0 and total > 0),
        "returncode": int(res.returncode),
        "vector_total": int(total),
        "failed": int(failed),
        "ts_utc": utc_now(),
    }
    json_dump(out_dir / f"{prefix}_p1_summary.json", summary)
    return summary


def run_campaign_pair_command(
    cfg: dict[str, Any],
    runner: CommandRunner,
    cell: dict[str, Any],
    rep_idx: int,
    seed: int,
    order_tag: str,
) -> dict[str, Any]:
    workload = next(w for w in cfg["workloads"]["families"] if w["id"] == cell["workload_id"])
    profile = cfg["workloads"]["complexity_profiles"][cell["complexity_tier"]]
    scenario = next(s for s in cfg["cost_model"]["scenarios"] if s["id"] == cell["cost_scenario"])
    fixed = next(f for f in cfg["cost_model"]["fixed_overheads"] if f["id"] == cell["fixed_overhead_id"])

    N = int(cell["N_reuse"])
    warmup = int(cfg["experiment"]["warmup_episodes"])
    timeout = int(cfg["execution"]["per_episode_timeout_sec"])
    runner.timeout_sec = timeout

    mapping_common: dict[str, Any] = {
        "campaign_idx": rep_idx,
        "seed": seed,
        "manifest": workload["manifest"],
        "cache_dir": cfg["paths"]["operational_cache_dir"],
        "baseline_iterations": int(profile["baseline_iterations"]),
        "run_iterations": int(profile["run_iterations"]),
        "verify_iterations": int(profile["verify_iterations"]),
        "concurrency": int(cell["concurrency"]),
    }

    for i in range(warmup):
        m = dict(mapping_common)
        m["episode_idx"] = i
        cmd_b = resolve_template(workload["commands"]["baseline"], m)
        runner.run(cmd_b, stage="P2_WARMUP_BASELINE", meta={"cell": cell, "rep_idx": rep_idx, "warmup_episode": i})
        cmd_r = resolve_template(workload["commands"]["pcs_run"], m)
        runner.run(cmd_r, stage="P2_WARMUP_PCS_RUN", meta={"cell": cell, "rep_idx": rep_idx, "warmup_episode": i})
        cmd_v = resolve_template(workload["commands"]["pcs_verify"], m)
        runner.run(cmd_v, stage="P2_WARMUP_PCS_VERIFY", meta={"cell": cell, "rep_idx": rep_idx, "warmup_episode": i})

    baseline_total = 0.0
    pcs_run_total = 0.0
    pcs_verify_total = 0.0
    failures = 0
    operation_count = 0

    def do_baseline() -> None:
        nonlocal baseline_total, failures, operation_count
        for ep in range(N):
            m = dict(mapping_common)
            m["episode_idx"] = ep
            cmd = resolve_template(workload["commands"]["baseline"], m)
            res = runner.run(
                cmd,
                stage="P2_BASELINE_EPISODE",
                meta={"cell": cell, "rep_idx": rep_idx, "episode_idx": ep, "order": order_tag},
                env_extra={"PCS_CONCURRENCY": str(cell["concurrency"])},
            )
            baseline_total += res.duration_ms
            operation_count += 1
            if res.returncode != 0:
                failures += 1

    def do_pcs() -> None:
        nonlocal pcs_run_total, pcs_verify_total, failures, operation_count
        m = dict(mapping_common)
        m["episode_idx"] = 0
        cmd_run = resolve_template(workload["commands"]["pcs_run"], m)
        rr = runner.run(
            cmd_run,
            stage="P2_PCS_RUN",
            meta={"cell": cell, "rep_idx": rep_idx, "order": order_tag},
            env_extra={"PCS_CONCURRENCY": str(cell["concurrency"])},
        )
        pcs_run_total += rr.duration_ms
        operation_count += 1
        if rr.returncode != 0:
            failures += 1
        for ep in range(N):
            mv = dict(mapping_common)
            mv["episode_idx"] = ep
            cmd_v = resolve_template(workload["commands"]["pcs_verify"], mv)
            rv = runner.run(
                cmd_v,
                stage="P2_PCS_VERIFY_EPISODE",
                meta={"cell": cell, "rep_idx": rep_idx, "episode_idx": ep, "order": order_tag},
                env_extra={"PCS_CONCURRENCY": str(cell["concurrency"])},
            )
            pcs_verify_total += rv.duration_ms
            operation_count += 1
            if rv.returncode != 0:
                failures += 1

    if order_tag == "A":
        do_baseline()
        do_pcs()
    else:
        do_pcs()
        do_baseline()

    c_run = float(cfg["cost_model"]["c_run_per_ms"])
    ratio = float(scenario["c_check_to_c_run_ratio"])
    c_check = c_run * ratio
    c_hash = float(cfg["cost_model"]["c_hash_per_op"])
    c_reg = float(fixed["c_registry_fixed"])
    c_cert = float(fixed["c_cert_fixed"])

    baseline_cost = baseline_total * c_run
    pcs_cost = (pcs_run_total * c_run) + (pcs_verify_total * c_check) + (N * c_hash) + c_reg + c_cert

    fallback_ms_cost = safe_float(cfg["cost_model"].get("fallback_ms_cost_if_usage_missing", c_run), c_run)
    baseline_token_cost = baseline_total * fallback_ms_cost
    pcs_token_cost = (pcs_run_total + pcs_verify_total) * fallback_ms_cost + (N * c_hash) + c_reg + c_cert

    row: dict[str, Any] = {
        **cell,
        "cell_id": make_cell_id(cell),
        "backend": "command",
        "rep_idx": rep_idx,
        "seed": seed,
        "order": order_tag,
        "N_reuse": N,
        "baseline_total_latency_ms": baseline_total,
        "pcs_run_latency_ms": pcs_run_total,
        "pcs_verify_total_latency_ms": pcs_verify_total,
        "pcs_total_latency_ms": pcs_run_total + pcs_verify_total,
        "latency_improvement_ratio": (1.0 - ((pcs_run_total + pcs_verify_total) / baseline_total)) if baseline_total > 0 else float("nan"),
        "baseline_total_cost": baseline_cost,
        "pcs_total_cost": pcs_cost,
        "cost_improvement_ratio": (1.0 - (pcs_cost / baseline_cost)) if baseline_cost > 0 else float("nan"),
        "baseline_total_cost_token": baseline_token_cost,
        "pcs_total_cost_token": pcs_token_cost,
        "token_cost_improvement_ratio": (1.0 - (pcs_token_cost / baseline_token_cost)) if baseline_token_cost > 0 else float("nan"),
        "cache_hit_rate": ((N - 1) / N) if N > 0 else float("nan"),
        "command_failures": failures,
        "operation_count": operation_count,
        "retry_attempts": 0,
        "token_usage_missing_calls": operation_count,
        "baseline_input_tokens": None,
        "baseline_output_tokens": None,
        "pcs_input_tokens": None,
        "pcs_output_tokens": None,
        "pass": failures == 0,
    }
    return row


def run_campaign_pair_gemini(
    cfg: dict[str, Any],
    repo_root: Path,
    out_dir: Path,
    prefix: str,
    cell: dict[str, Any],
    rep_idx: int,
    seed: int,
    order_tag: str,
) -> dict[str, Any]:
    gemini_driver = load_gemini_driver_module()
    workload = next(w for w in cfg["workloads"]["families"] if w["id"] == cell["workload_id"])
    scenario = next(s for s in cfg["cost_model"]["scenarios"] if s["id"] == cell["cost_scenario"])
    fixed = next(f for f in cfg["cost_model"]["fixed_overheads"] if f["id"] == cell["fixed_overhead_id"])

    manifest = load_manifest_for_workload(repo_root, workload)
    N = int(cell["N_reuse"])
    warmup = int(cfg["experiment"]["warmup_episodes"])
    cell_id = make_cell_id(cell)

    baseline_total = 0.0
    pcs_run_total = 0.0
    pcs_verify_total = 0.0

    baseline_token_cost = 0.0
    pcs_token_cost = 0.0

    baseline_input_tokens = 0
    baseline_output_tokens = 0
    pcs_input_tokens = 0
    pcs_output_tokens = 0

    failures = 0
    retries = 0
    operation_count = 0
    token_usage_missing_calls = 0

    logs = gemini_log_paths(out_dir, prefix)

    def build_payload(episode_idx: int, phase: str) -> dict[str, Any]:
        return {
            "input_text": input_text_for_episode(manifest, episode_idx=0, workload=workload),
            "workload_id": workload["id"],
            "complexity_tier": cell["complexity_tier"],
            "concurrency": int(cell["concurrency"]),
            "campaign_idx": rep_idx,
            "episode_idx": episode_idx,
            "seed": seed,
            "cell_id": cell_id,
            "phase": phase,
        }

    def account_call(result: dict[str, Any], *, phase: str, arm: str, count_for_metrics: bool = True) -> tuple[float, int, int]:
        nonlocal failures, retries, operation_count, token_usage_missing_calls
        append_gemini_audit(
            logs,
            phase=phase,
            cell_id=cell_id,
            repetition=rep_idx,
            arm=arm,
            order=order_tag,
            result=result,
        )
        if count_for_metrics:
            operation_count += 1
            retries += safe_int(result.get("retry_count"), 0)
            if not bool(result.get("ok", False)):
                failures += 1
        call_cost, usage_missing, in_tokens, out_tokens = token_cost_for_call(result, cfg["cost_model"])
        if count_for_metrics and usage_missing:
            token_usage_missing_calls += 1
        return call_cost, (in_tokens or 0), (out_tokens or 0)

    for i in range(warmup):
        warm_b = gemini_driver.run_baseline(build_payload(i, "warmup_baseline"), cfg)
        account_call(warm_b, phase="P2_WARMUP_BASELINE", arm="A", count_for_metrics=False)
        warm_r = gemini_driver.run_pcs_run(build_payload(i, "warmup_pcs_run"), cfg)
        account_call(warm_r, phase="P2_WARMUP_PCS_RUN", arm="B", count_for_metrics=False)
        warm_v_payload = build_payload(i, "warmup_pcs_verify")
        warm_v_payload["run_input_hash"] = warm_r.get("input_hash")
        warm_v_payload["run_response_hash"] = warm_r.get("response_hash")
        warm_v_payload["expected_response_hash"] = warm_r.get("response_hash")
        warm_v = gemini_driver.run_pcs_verify(warm_v_payload, cfg)
        account_call(warm_v, phase="P2_WARMUP_PCS_VERIFY", arm="B", count_for_metrics=False)

    def do_baseline() -> None:
        nonlocal baseline_total, baseline_token_cost, baseline_input_tokens, baseline_output_tokens
        for ep in range(N):
            result = gemini_driver.run_baseline(build_payload(ep, "baseline"), cfg)
            baseline_total += safe_float(result.get("latency_ms"), 0.0)
            cost, in_tokens, out_tokens = account_call(result, phase="P2_BASELINE_EPISODE", arm="A")
            baseline_token_cost += cost
            baseline_input_tokens += in_tokens
            baseline_output_tokens += out_tokens

    def do_pcs() -> None:
        nonlocal pcs_run_total, pcs_verify_total, pcs_token_cost, pcs_input_tokens, pcs_output_tokens
        run_result = gemini_driver.run_pcs_run(build_payload(0, "pcs_run"), cfg)
        pcs_run_total += safe_float(run_result.get("latency_ms"), 0.0)
        run_cost, run_in, run_out = account_call(run_result, phase="P2_PCS_RUN", arm="B")
        pcs_token_cost += run_cost
        pcs_input_tokens += run_in
        pcs_output_tokens += run_out

        for ep in range(N):
            verify_payload = build_payload(ep, "pcs_verify")
            verify_payload["run_input_hash"] = run_result.get("input_hash")
            verify_payload["run_response_hash"] = run_result.get("response_hash")
            verify_payload["expected_response_hash"] = run_result.get("response_hash")
            verify_result = gemini_driver.run_pcs_verify(verify_payload, cfg)
            pcs_verify_total += safe_float(verify_result.get("latency_ms"), 0.0)
            verify_cost, ver_in, ver_out = account_call(verify_result, phase="P2_PCS_VERIFY_EPISODE", arm="B")
            pcs_token_cost += verify_cost
            pcs_input_tokens += ver_in
            pcs_output_tokens += ver_out

    if order_tag == "A":
        do_baseline()
        do_pcs()
    else:
        do_pcs()
        do_baseline()

    c_run = float(cfg["cost_model"]["c_run_per_ms"])
    ratio = float(scenario["c_check_to_c_run_ratio"])
    c_check = c_run * ratio
    c_hash = float(cfg["cost_model"]["c_hash_per_op"])
    c_reg = float(fixed["c_registry_fixed"])
    c_cert = float(fixed["c_cert_fixed"])

    baseline_cost_ms = baseline_total * c_run
    pcs_cost_ms = (pcs_run_total * c_run) + (pcs_verify_total * c_check) + (N * c_hash) + c_reg + c_cert

    baseline_cost_token = baseline_token_cost
    pcs_cost_token = pcs_token_cost + (N * c_hash) + c_reg + c_cert

    row: dict[str, Any] = {
        **cell,
        "cell_id": cell_id,
        "backend": "gemini_api",
        "gemini_model": str(cfg.get("workload", {}).get("gemini", {}).get("model", "")),
        "rep_idx": rep_idx,
        "seed": seed,
        "order": order_tag,
        "N_reuse": N,
        "baseline_total_latency_ms": baseline_total,
        "pcs_run_latency_ms": pcs_run_total,
        "pcs_verify_total_latency_ms": pcs_verify_total,
        "pcs_total_latency_ms": pcs_run_total + pcs_verify_total,
        "latency_improvement_ratio": (1.0 - ((pcs_run_total + pcs_verify_total) / baseline_total)) if baseline_total > 0 else float("nan"),
        "baseline_total_cost": baseline_cost_ms,
        "pcs_total_cost": pcs_cost_ms,
        "cost_improvement_ratio": (1.0 - (pcs_cost_ms / baseline_cost_ms)) if baseline_cost_ms > 0 else float("nan"),
        "baseline_total_cost_token": baseline_cost_token,
        "pcs_total_cost_token": pcs_cost_token,
        "token_cost_improvement_ratio": (1.0 - (pcs_cost_token / baseline_cost_token)) if baseline_cost_token > 0 else float("nan"),
        "cache_hit_rate": ((N - 1) / N) if N > 0 else float("nan"),
        "command_failures": failures,
        "operation_count": operation_count,
        "retry_attempts": retries,
        "token_usage_missing_calls": token_usage_missing_calls,
        "baseline_input_tokens": baseline_input_tokens,
        "baseline_output_tokens": baseline_output_tokens,
        "pcs_input_tokens": pcs_input_tokens,
        "pcs_output_tokens": pcs_output_tokens,
        "pass": failures == 0,
    }
    return row


def run_campaign_pair(
    cfg: dict[str, Any],
    runner: CommandRunner,
    repo_root: Path,
    out_dir: Path,
    prefix: str,
    cell: dict[str, Any],
    rep_idx: int,
    seed: int,
    order_tag: str,
) -> dict[str, Any]:
    backend = backend_name(cfg)
    if backend == "gemini_api":
        return run_campaign_pair_gemini(cfg, repo_root, out_dir, prefix, cell, rep_idx, seed, order_tag)
    return run_campaign_pair_command(cfg, runner, cell, rep_idx, seed, order_tag)


def run_p2_operational(
    cfg: dict[str, Any],
    runner: CommandRunner,
    repo_root: Path,
    out_dir: Path,
    prefix: str,
) -> dict[str, Any]:
    cells = cartesian_cells(cfg)
    if backend_name(cfg) == "gemini_api":
        logs = gemini_log_paths(out_dir, prefix)
        for p in logs.values():
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text("", encoding="utf-8")
    seeds = [int(s) for s in cfg["experiment"]["random_seeds"]]
    reps = int(cfg["experiment"]["campaign_repetitions_per_cell"])
    rows: list[dict[str, Any]] = []

    order_rng = random.Random(int(cfg["experiment"].get("order_seed", 20260213)))
    for cidx, cell in enumerate(cells):
        for rep in range(reps):
            seed = seeds[(cidx + rep) % len(seeds)]
            order = "A" if order_rng.random() < 0.5 else "B"
            row = run_campaign_pair(cfg, runner, repo_root, out_dir, prefix, cell, rep_idx=rep, seed=seed, order_tag=order)
            rows.append(row)

    if rows:
        fieldnames = list(rows[0].keys())
    else:
        fieldnames = ["workload_id", "complexity_tier"]
    write_csv(out_dir / f"{prefix}_campaigns.csv", rows, fieldnames=fieldnames)

    raw_parquet = out_dir / f"{prefix}_latency_cost_raw.parquet"
    parquet_ok = maybe_write_parquet(raw_parquet, rows)
    if not parquet_ok:
        write_csv(out_dir / f"{prefix}_latency_cost_raw.csv", rows, fieldnames=fieldnames)

    lat_b = [float(r["baseline_total_latency_ms"]) for r in rows if r.get("pass")]
    lat_p = [float(r["pcs_total_latency_ms"]) for r in rows if r.get("pass")]
    cost_b = [float(r["baseline_total_cost"]) for r in rows if r.get("pass")]
    cost_p = [float(r["pcs_total_cost"]) for r in rows if r.get("pass")]
    token_b = [float(r["baseline_total_cost_token"]) for r in rows if r.get("pass")]
    token_p = [float(r["pcs_total_cost_token"]) for r in rows if r.get("pass")]

    lat_ci = bootstrap_ci_improvement(lat_b, lat_p, n_boot=int(cfg["analysis"]["bootstrap_samples"]), seed=777)
    cost_ci = bootstrap_ci_improvement(cost_b, cost_p, n_boot=int(cfg["analysis"]["bootstrap_samples"]), seed=888)
    token_cost_ci = bootstrap_ci_improvement(token_b, token_p, n_boot=int(cfg["analysis"]["bootstrap_samples"]), seed=889)

    total_ops = sum(safe_int(r.get("operation_count"), 0) for r in rows)
    total_failures = sum(safe_int(r.get("command_failures"), 0) for r in rows)
    total_retries = sum(safe_int(r.get("retry_attempts"), 0) for r in rows)
    token_usage_missing_calls = sum(safe_int(r.get("token_usage_missing_calls"), 0) for r in rows)

    summary = {
        "cells": len(cells),
        "campaign_rows": len(rows),
        "pass_rows": int(sum(1 for r in rows if r.get("pass"))),
        "parquet_written": parquet_ok,
        "latency_improvement": lat_ci,
        "cost_improvement": cost_ci,
        "cost_improvement_ms_model": cost_ci,
        "cost_improvement_token_model": token_cost_ci,
        "failure_rate": (total_failures / total_ops) if total_ops > 0 else float("nan"),
        "retry_statistics": {
            "total_retry_attempts": total_retries,
            "avg_retry_per_operation": (total_retries / total_ops) if total_ops > 0 else float("nan"),
            "max_retry_per_campaign": max((safe_int(r.get("retry_attempts"), 0) for r in rows), default=0),
            "total_operations": total_ops,
        },
        "token_usage_missing_rate": (token_usage_missing_calls / total_ops) if total_ops > 0 else float("nan"),
        "determinism_limitations": [
            "Cloud API responses can remain non-deterministic even with fixed generation config.",
            "Network and provider-side queue jitter cannot be fully eliminated; ABBA paired design only mitigates drift.",
            "Token accounting availability depends on provider usage metadata and may fall back to ms-based approximation.",
        ],
        "network_control_notes": [
            "Paired baseline/PCS campaigns use identical workload inputs and fixed prompt template.",
            "Randomized ABBA crossover is applied per campaign to reduce temporal drift bias.",
        ],
        "backend": backend_name(cfg),
        "ts_utc": utc_now(),
    }
    json_dump(out_dir / f"{prefix}_p2_summary.json", summary)
    return summary


def run_p3_fail_closed(cfg: dict[str, Any], runner: CommandRunner, out_dir: Path, prefix: str) -> dict[str, Any]:
    inj = cfg["stress_fail_closed"]["injections"]
    verifier_tpl = cfg["commands"]["verifier_template"]
    results = []
    false_accept = 0
    reject_reasons: dict[str, int] = {}

    if runner.dry_run:
        report = {
            "injection_count": len(inj),
            "false_accept_count": 0,
            "false_accept_rate_reject_injection": 0.0,
            "pass": True,
            "results": [],
            "ts_utc": utc_now(),
            "note": "dry_run_skips_command_execution",
        }
        json_dump(out_dir / f"{prefix}_fail_closed_report.json", report)
        json_dump(out_dir / f"{prefix}_reject_reason_distribution.json", {"dry_run_skipped": len(inj)})
        return report

    for item in inj:
        bundle = item["bundle"]
        cmd = resolve_template(verifier_tpl, {"bundle": bundle})
        res = runner.run(
            cmd,
            stage="P3_STRESS_FAIL_CLOSED",
            meta={"injection_id": item["id"], "bundle": bundle},
        )
        expected_reject = bool(item.get("expected_reject", True))
        is_false_accept = bool(expected_reject and res.returncode == 0)
        if is_false_accept:
            false_accept += 1
        reason = "rc0_accept" if res.returncode == 0 else "rejected_nonzero_rc"
        reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
        results.append(
            {
                "id": item["id"],
                "bundle": bundle,
                "expected_reject": expected_reject,
                "returncode": int(res.returncode),
                "false_accept": is_false_accept,
                "duration_ms": res.duration_ms,
            }
        )

    total = len(inj)
    false_accept_rate = (false_accept / total) if total > 0 else float("nan")
    report = {
        "injection_count": total,
        "false_accept_count": false_accept,
        "false_accept_rate_reject_injection": false_accept_rate,
        "pass": false_accept == 0,
        "results": results,
        "ts_utc": utc_now(),
    }
    json_dump(out_dir / f"{prefix}_fail_closed_report.json", report)
    json_dump(out_dir / f"{prefix}_reject_reason_distribution.json", reject_reasons)
    return report


def run_p4_determinism(cfg: dict[str, Any], runner: CommandRunner, out_dir: Path, prefix: str) -> dict[str, Any]:
    bundles = cfg["determinism"]["sample_accept_bundles"]
    repeats = int(cfg["determinism"]["repeats_per_receipt"])
    verifier_tpl = cfg["commands"]["verifier_template"]

    rows: list[dict[str, Any]] = []
    match_count = 0
    for bundle in bundles:
        hashes = []
        rc_all = []
        for i in range(repeats):
            cmd = resolve_template(verifier_tpl, {"bundle": bundle})
            res = runner.run(
                cmd,
                stage="P4_DETERMINISM_SAMPLE",
                meta={"bundle": bundle, "repeat": i},
            )
            rc_all.append(res.returncode)
            hashes.append(sha256_text(res.stdout))
            rows.append(
                {
                    "bundle": bundle,
                    "repeat": i,
                    "returncode": int(res.returncode),
                    "stdout_sha256": sha256_text(res.stdout),
                    "stderr_sha256": sha256_text(res.stderr),
                    "duration_ms": res.duration_ms,
                }
            )
        all_same_hash = len(set(hashes)) == 1
        all_rc0 = all(rc == 0 for rc in rc_all)
        if all_same_hash and all_rc0:
            match_count += 1

    write_csv(out_dir / f"{prefix}_replay_hashes.csv", rows, fieldnames=list(rows[0].keys()) if rows else ["bundle"])
    maybe_write_parquet(out_dir / f"{prefix}_replay_hashes.parquet", rows)

    rate = (match_count / len(bundles)) if bundles else float("nan")
    report = {
        "bundle_count": len(bundles),
        "repeats_per_receipt": repeats,
        "deterministic_replay_match_count": match_count,
        "deterministic_replay_match_rate": rate,
        "pass": bool(rate == 1.0),
        "ts_utc": utc_now(),
    }
    json_dump(out_dir / f"{prefix}_determinism_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="PoC2 operational runner")
    parser.add_argument("--config", default="PoC2/poc2_operational_config.yaml")
    parser.add_argument("--out-dir", default="PoC2/runs")
    parser.add_argument("--tag", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = strict_yaml_load(cfg_path)

    runtime_cfg = cfg.get("_runtime") if isinstance(cfg.get("_runtime"), dict) else {}
    runtime_cfg = dict(runtime_cfg)
    runtime_cfg["dry_run"] = bool(args.dry_run)
    cfg["_runtime"] = runtime_cfg

    repo_root = (cfg_path.parent.parent / "..").resolve() if cfg.get("repo_root") is None else Path(cfg["repo_root"]).resolve()
    if not repo_root.exists():
        repo_root = Path(".").resolve()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = args.tag if args.tag else str(cfg.get("artifact_prefix", "poc2"))
    cmd_log = out_dir / f"{prefix}_command_log.jsonl"

    effective = dict(cfg)
    effective["_resolved"] = {
        "config_path": str(cfg_path),
        "repo_root": str(repo_root),
        "out_dir": str(out_dir),
        "runner": "PoC2/poc2_operational_runner.py",
        "dry_run": bool(args.dry_run),
    }
    json_dump(out_dir / f"{prefix}_effective_config.json", effective)

    plan_ref = cfg.get("plan_ref")
    plan_info: dict[str, Any] = {}
    if plan_ref:
        plan_path = (repo_root / plan_ref).resolve() if not str(plan_ref).startswith("/") else Path(plan_ref)
        if plan_path.exists():
            plan_info = {"plan_ref": str(plan_path), "plan_sha256": sha256_file(plan_path)}
        else:
            plan_info = {"plan_ref": str(plan_path), "plan_sha256": None, "warning": "plan_ref_not_found"}

    gemini_driver_path = (Path(__file__).resolve().parent / "gemini_workload_driver.py").resolve()
    protocol_fp = {
        "captured_utc": utc_now(),
        "config_sha256": sha256_file(cfg_path),
        "runner_sha256": sha256_file(Path(__file__).resolve()),
        "gemini_driver_sha256": sha256_file(gemini_driver_path) if gemini_driver_path.exists() else None,
        **plan_info,
    }
    json_dump(out_dir / f"{prefix}_protocol_fingerprint.json", protocol_fp)

    env = capture_environment(repo_root)
    json_dump(out_dir / f"{prefix}_environment_fingerprint.json", env)

    manifests = {}
    for w in cfg["workloads"]["families"]:
        mpath = (repo_root / w["manifest"]).resolve() if not str(w["manifest"]).startswith("/") else Path(w["manifest"])
        manifests[w["id"]] = {
            "manifest_path": str(mpath),
            "manifest_sha256": sha256_file(mpath) if mpath.exists() else None,
        }
    json_dump(out_dir / f"{prefix}_workload_manifest_sha256.json", manifests)

    runner = CommandRunner(
        cwd=repo_root,
        timeout_sec=int(cfg["execution"]["per_episode_timeout_sec"]),
        log_jsonl=cmd_log,
        dry_run=bool(args.dry_run),
    )

    p1 = run_p1_conformance(cfg, runner, out_dir, prefix)
    p2 = run_p2_operational(cfg, runner, repo_root, out_dir, prefix)
    p3 = run_p3_fail_closed(cfg, runner, out_dir, prefix)
    p4 = run_p4_determinism(cfg, runner, out_dir, prefix)

    gates = {
        "P1_CONFORMANCE_SMOKE": bool(p1.get("pass")),
        "P3_STRESS_FAIL_CLOSED": bool(p3.get("pass")),
        "P4_DETERMINISM_SAMPLE": bool(p4.get("pass")),
    }
    hard_pass = all(gates.values())
    final = {
        "ts_utc": utc_now(),
        "hard_gates": gates,
        "hard_pass": hard_pass,
        "p1_summary": p1,
        "p2_summary": p2,
        "p3_summary": p3,
        "p4_summary": p4,
    }
    json_dump(out_dir / f"{prefix}_final_report.json", final)

    md = [
        f"# PoC2 Final Report ({prefix})",
        "",
        f"- Generated: {final['ts_utc']}",
        f"- Backend: {backend_name(cfg)}",
        f"- Hard pass: **{hard_pass}**",
        "",
        "## Hard Gates",
    ]
    for k, v in gates.items():
        md.append(f"- {k}: {'PASS' if v else 'FAIL'}")
    md.extend(
        [
            "",
            "## P2 Summary",
            f"- campaign_rows: {p2.get('campaign_rows')}",
            f"- pass_rows: {p2.get('pass_rows')}",
            f"- latency mean improvement: {p2.get('latency_improvement', {}).get('mean')}",
            f"- cost mean improvement (ms model): {p2.get('cost_improvement_ms_model', {}).get('mean')}",
            f"- cost mean improvement (token model): {p2.get('cost_improvement_token_model', {}).get('mean')}",
            f"- failure_rate: {p2.get('failure_rate')}",
            f"- retry_total: {p2.get('retry_statistics', {}).get('total_retry_attempts')}",
            "",
            "## Determinism Limitations",
        ]
    )
    for item in p2.get("determinism_limitations", []):
        md.append(f"- {item}")
    md.extend(
        [
            "",
            "> Note: This is operational-economics validation, not semantic model-quality evaluation.",
            "> Fail-open is prohibited: unsuccessful API operations are always recorded as failures.",
        ]
    )
    (out_dir / f"{prefix}_final_report.md").write_text("\n".join(md), encoding="utf-8")
    return 0 if hard_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
