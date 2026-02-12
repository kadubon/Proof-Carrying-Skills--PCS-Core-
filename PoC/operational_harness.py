from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _kdf_hex(data: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", data, b"pcs-operational-harness", iterations).hex()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _cache_path(cache_dir: Path, workload_id: str, campaign_idx: int, seed: int) -> Path:
    safe_id = workload_id.replace("/", "_").replace("\\", "_")
    return cache_dir / f"{safe_id}_campaign{campaign_idx}_seed{seed}.json"


def _validate_manifest(manifest: dict[str, Any]) -> None:
    _require(isinstance(manifest, dict), "manifest must be object")
    _require(isinstance(manifest.get("manifest_version"), str) and manifest["manifest_version"], "manifest_version required")
    _require(isinstance(manifest.get("workload_id"), str) and manifest["workload_id"], "workload_id required")
    _require(isinstance(manifest.get("shared_input"), str) and manifest["shared_input"], "shared_input required")
    episodes = manifest.get("episodes")
    _require(isinstance(episodes, list) and len(episodes) > 0, "episodes must be non-empty array")
    for idx, item in enumerate(episodes):
        _require(isinstance(item, dict), f"episodes[{idx}] must be object")
        _require(item.get("episode_idx") == idx, f"episodes[{idx}].episode_idx must equal index")
        _require(isinstance(item.get("reuse_tag"), str) and item["reuse_tag"], f"episodes[{idx}].reuse_tag required")


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic operational harness for E6 command-mode experiments")
    parser.add_argument("--mode", choices=["baseline", "pcs-run", "pcs-verify"], required=True)
    parser.add_argument("--campaign", type=int, required=True)
    parser.add_argument("--episode", type=int, default=-1)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--manifest", default="PoC/workloads/operational_input_manifest.json")
    parser.add_argument("--cache-dir", default="PoC/runs/operational_cache")
    parser.add_argument("--baseline-iterations", type=int, default=60000)
    parser.add_argument("--run-iterations", type=int, default=60000)
    parser.add_argument("--verify-iterations", type=int, default=1000)
    args = parser.parse_args()

    if args.campaign < 0:
        raise ValueError("campaign must be >= 0")
    if args.seed < 0:
        raise ValueError("seed must be >= 0")
    if args.baseline_iterations <= 0 or args.run_iterations <= 0 or args.verify_iterations <= 0:
        raise ValueError("iteration counts must be > 0")

    manifest_path = Path(args.manifest)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_json(manifest_path)
    _validate_manifest(manifest)

    episodes = manifest["episodes"]
    if args.mode in {"baseline", "pcs-verify"}:
        if args.episode < 0 or args.episode >= len(episodes):
            raise ValueError(f"episode out of range: {args.episode} (0..{len(episodes)-1})")

    workload_id = manifest["workload_id"]
    manifest_sha256 = _sha256_file(manifest_path)

    campaign_payload = (
        f"workload={workload_id}|manifest={manifest_sha256}|campaign={args.campaign}|seed={args.seed}|"
        f"shared_input={manifest['shared_input']}"
    ).encode("utf-8")

    if args.mode == "baseline":
        episode_tag = episodes[args.episode]["reuse_tag"]
        payload = campaign_payload + f"|episode_tag={episode_tag}".encode("utf-8")
        digest = _kdf_hex(payload, args.baseline_iterations)
        out = {
            "mode": "baseline",
            "campaign_idx": args.campaign,
            "episode_idx": args.episode,
            "seed": args.seed,
            "workload_id": workload_id,
            "manifest_sha256": manifest_sha256,
            "digest": digest,
            "iterations": args.baseline_iterations,
        }
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
        return 0

    cache_path = _cache_path(cache_dir, workload_id, args.campaign, args.seed)

    if args.mode == "pcs-run":
        run_digest = _kdf_hex(campaign_payload, args.run_iterations)
        cache_obj = {
            "workload_id": workload_id,
            "campaign_idx": args.campaign,
            "seed": args.seed,
            "manifest_sha256": manifest_sha256,
            "run_digest": run_digest,
            "run_iterations": args.run_iterations,
        }
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps(cache_obj, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(cache_path)
        out = {
            "mode": "pcs-run",
            "campaign_idx": args.campaign,
            "seed": args.seed,
            "workload_id": workload_id,
            "manifest_sha256": manifest_sha256,
            "cache_path": str(cache_path).replace("\\", "/"),
            "run_digest": run_digest,
            "iterations": args.run_iterations,
        }
        print(json.dumps(out, ensure_ascii=False, sort_keys=True))
        return 0

    if not cache_path.exists():
        raise ValueError(f"pcs-verify requires cache file from pcs-run: {cache_path}")

    cache_obj = _load_json(cache_path)
    _require(cache_obj.get("workload_id") == workload_id, "cache workload_id mismatch")
    _require(cache_obj.get("campaign_idx") == args.campaign, "cache campaign mismatch")
    _require(cache_obj.get("seed") == args.seed, "cache seed mismatch")
    _require(cache_obj.get("manifest_sha256") == manifest_sha256, "cache manifest hash mismatch")

    run_digest = cache_obj.get("run_digest")
    _require(isinstance(run_digest, str) and len(run_digest) == 64, "cache run_digest invalid")

    episode_tag = episodes[args.episode]["reuse_tag"]
    verify_payload = f"run_digest={run_digest}|episode_tag={episode_tag}".encode("utf-8")
    verify_digest = _kdf_hex(verify_payload, args.verify_iterations)

    out = {
        "mode": "pcs-verify",
        "campaign_idx": args.campaign,
        "episode_idx": args.episode,
        "seed": args.seed,
        "workload_id": workload_id,
        "manifest_sha256": manifest_sha256,
        "cache_path": str(cache_path).replace("\\", "/"),
        "verify_digest": verify_digest,
        "iterations": args.verify_iterations,
    }
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)