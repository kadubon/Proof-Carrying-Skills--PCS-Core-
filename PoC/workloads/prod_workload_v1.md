# Workload Disclosure: prod_workload_v1

## Purpose

Evaluate E6 command-mode claim readiness under a fully disclosed, reproducible wrapper workload.
This pilot measures operational behavior for repeated acceptance of a deterministic black-box task.

## Dataset / Input Construction

- source: local synthetic workload manifest (`PoC/workloads/operational_input_manifest.json`)
- date range: generated on February 12, 2026 (UTC)
- inclusion criteria: all listed episodes (0..99)
- exclusion criteria: none
- sample count: 100 episodes per campaign
- random seed policy: seed passed explicitly by E6 runner (`{seed}` placeholder)

## Input Manifest

- manifest path: `PoC/workloads/operational_input_manifest.json`
- `input_manifest_sha256`: `57fa9927da01394a62ebd42bb6330ee6a721f2a168a202f25af0cf3cbac627c1`
- generation command: `python - << script that writes operational_input_manifest.json with 100 episodes >>` (captured in repository history)

## Harness

- harness repository: current PCS-Core repository
- harness commit: captured in E6 environment fingerprint (`git_head`)
- harness version: `PoC/operational_harness.py@v1`
- runtime dependencies: Python 3.13+, standard library only

## Baseline vs PCS Conditions

- baseline mode command template:
  - `python PoC/operational_harness.py --mode baseline --campaign {campaign_idx} --episode {episode_idx} --seed {seed} --manifest PoC/workloads/operational_input_manifest.json --cache-dir PoC/runs/operational_cache --baseline-iterations 60000 --run-iterations 60000 --verify-iterations 1000`
- pcs run command template:
  - `python PoC/operational_harness.py --mode pcs-run --campaign {campaign_idx} --seed {seed} --manifest PoC/workloads/operational_input_manifest.json --cache-dir PoC/runs/operational_cache --baseline-iterations 60000 --run-iterations 60000 --verify-iterations 1000`
- pcs verify command template:
  - `python PoC/operational_harness.py --mode pcs-verify --campaign {campaign_idx} --episode {episode_idx} --seed {seed} --manifest PoC/workloads/operational_input_manifest.json --cache-dir PoC/runs/operational_cache --baseline-iterations 60000 --run-iterations 60000 --verify-iterations 1000`
- hardware / instance type: captured in `PoC/runs/e6_optional_cost_environment_fingerprint.json`
- concurrency policy: single-process sequential command execution
- warmup policy: 1 warmup cycle in command scenario

## Known Threats to Validity

- potential confounders:
  - Python process startup overhead contributes to measured latency
  - synthetic deterministic workload may not represent all production LLM behaviors
  - local machine load can influence wall-clock timings
- mitigation:
  - fixed workload manifest and explicit seeds
  - command logs + environment fingerprint retained for audit
  - claim-readiness criteria require command-mode success and positive CI lower bounds

## Reproducibility Bundle

- command logs path: `PoC/runs/e6_optional_cost_command_log.jsonl`
- environment fingerprint path: `PoC/runs/e6_optional_cost_environment_fingerprint.json`
- effective config path: `PoC/runs/e6_optional_cost_effective_config.json`
- report path: `PoC/runs/e6_optional_cost_report.md`