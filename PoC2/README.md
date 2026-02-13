# PoC2 Operational Benchmark (Gemini API)

PoC2 measures PCS operational economics under real cloud LLM calls with a fail-closed policy.

## Scope

- Purpose: compare `baseline` (recompute every request) vs `PCS` (one run + repeated deterministic verify).
- Focus: latency/cost behavior and safety gates, not model quality uplift claims.
- Safety: API failures are never treated as success.

## Setup

```powershell
python -m pip install pyyaml google-genai
```

Set API key (priority order in runtime resolution: `GEMINI_API_KEY` then `GOOGLE_API_KEY`):

```powershell
$env:GEMINI_API_KEY = "YOUR_KEY"
```

Alternative: place keys in `PoC2/.env` (loaded automatically by PoC2 driver).

Do not commit keys. `.env` and PoC2 secret patterns are ignored by `.gitignore`.

## Run

Primary command:

```powershell
python PoC2/poc2_operational_runner.py --config PoC2/poc2_operational_config.yaml --out-dir PoC2/runs --tag poc2_gemini
```

Dry-run (no Gemini API calls):

```powershell
python PoC2/poc2_operational_runner.py --config PoC2/poc2_operational_config.yaml --out-dir PoC2/runs --tag poc2_gemini_dry --dry-run
```

## Staged Production-Load Plan

For production-load escalation, use:

- rollout plan: `PoC2/poc2_production_rollout_plan.yaml`
- stage profiles: `PoC2/poc2_stage_profiles.yaml`
- config generator: `PoC2/generate_stage_configs.py`

Generate all stage configs:

```powershell
python PoC2/generate_stage_configs.py --base-config PoC2/poc2_operational_config.yaml --profiles PoC2/poc2_stage_profiles.yaml --out-dir PoC2/staged-configs
```

Run a specific stage:

```powershell
python PoC2/poc2_operational_runner.py --config PoC2/staged-configs/poc2_s1_pilot.yaml --out-dir PoC2/runs --tag poc2_s1_pilot
```

Recommended order:

1. `S0_CONNECTIVITY`
2. `S1_PILOT`
3. `S2_PREPROD`
4. `S3_FULL_EVIDENCE`

Promotion to the next stage must follow gates defined in `PoC2/poc2_production_rollout_plan.yaml`.

Generated config filenames:

- `PoC2/staged-configs/poc2_s0_connectivity.yaml`
- `PoC2/staged-configs/poc2_s1_pilot.yaml`
- `PoC2/staged-configs/poc2_s2_preprod.yaml`
- `PoC2/staged-configs/poc2_s3_full_evidence.yaml`

## Artifacts

Main outputs under `PoC2/runs/`:

- `<tag>_final_report.json`
- `<tag>_final_report.md`
- `<tag>_campaigns.csv`
- `<tag>_p2_summary.json`
- `<tag>_command_log.jsonl`
- `<tag>_gemini_requests.jsonl`
- `<tag>_gemini_responses.jsonl`
- `<tag>_gemini_errors.jsonl`

Gemini audit JSONL records include:

- `timestamp_utc`, `phase`, `cell_id`, `repetition`, `arm`, `model`
- `request_hash`, `latency_ms`, `token_usage`, `status_code`, `ok`, `error_code`

No API keys or raw prompt/response bodies are logged.

## Cost Models

PoC2 reports both:

- ms-based cost (`c_run_per_ms`, `c_check_to_c_run_ratio`, fixed overheads)
- token-based cost (`input_token_usd`, `output_token_usd`)

If token usage metadata is missing, fallback cost uses `fallback_ms_cost_if_usage_missing`.

## Reproducibility Notes

- Fixed prompt template and fixed generation config are enforced in config.
- ABBA randomized crossover is used to reduce time-drift bias.
- Determinism limitations remain for cloud APIs:
  - provider-side non-determinism
  - network/queue jitter
  - rate-limit and transient server effects

## Engineer-Facing Reporting Rules

When sharing results, include all of the following:

- command line and stage config path
- `config_sha256`, `runner_sha256`, `gemini_driver_sha256`
- latency improvement CI
- cost improvement CI (ms model and token model)
- failure/retry statistics
- token-usage-missing rate
- limitations and negative/zero-gain regions

Do not claim production gain outside the measured envelope.
