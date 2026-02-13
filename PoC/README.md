# PoC Experiments

This folder contains experiment protocols and scripts for reproducibility claims.

## E6 Optional Cost/Latency Frame

`E6_OPTIONAL_COST` is intentionally separated from PCS-Core correctness claims.
It tests external end-to-end operational impact and is non-gating for verifier correctness.

### Why Separate

PCS-Core validates deterministic acceptance/rejection under strict bounds.
Operational cost or latency claims also depend on wrapper/runtime/deployment conditions that are external to the checker.

### Paper Alignment

The analysis follows the paper cost terms:

- `CostPCS = Costrun + Costcheck + Costhash + Costregistry`
- amortization term: `Costcert / E[Nreuse]`
- benefit condition: `E[CostPCS] + Costcert/E[Nreuse] < E[Costfull]`

### Run

```powershell
python PoC/e6_optional_cost_runner.py --config PoC/e6_optional_cost_config.example.json --out-dir PoC/runs --tag e6_optional_cost
```

### Outputs

- `PoC/runs/e6_optional_cost_campaigns.csv`
- `PoC/runs/e6_optional_cost_report.json`
- `PoC/runs/e6_optional_cost_report.md`
- `PoC/runs/e6_optional_cost_effective_config.json`
- `PoC/runs/e6_optional_cost_command_log.jsonl`
- `PoC/runs/e6_optional_cost_environment_fingerprint.json`

### Command-Mode Requirements (Operational Claims)

Workload disclosure template: `PoC/workloads/prod_workload_v1.md`

Default thresholds in example config: `min_claim_campaigns=30`, `min_claim_episodes=100`.

Operational pilot harness in this repository:
- `PoC/operational_harness.py`
- `PoC/workloads/operational_input_manifest.json`

## PoC2 Cloud LLM Operational Benchmark

Gemini API-based operational experiments are provided in `PoC2/`.

- runner: `PoC2/poc2_operational_runner.py`
- config: `PoC2/poc2_operational_config.yaml`
- guide: `PoC2/README.md`

PoC2 keeps fail-closed handling, ABBA paired crossover, and emits Gemini audit logs (`requests`, `responses`, `errors`) for external review.


- Provide workload disclosure metadata in config:
  - `workload_id`
  - `construction_doc`
  - `input_manifest_sha256`
  - `harness_version`
- Use auditable command templates with placeholders:
  - `{campaign_idx}`
  - `{episode_idx}`
  - `{seed}`
- Keep all runs (including failures/null results) and publish command logs.

### Claim Rules

- Do not treat simulated scenarios as operational evidence.
- Keep E6 conclusions explicitly separate from core verifier safety/correctness claims.
- Operational claim readiness is true only when command mode satisfies:
  - disclosure completeness
  - sufficient sample size (`min_claim_campaigns`, `min_claim_episodes`)
  - positive 95% CI lower bounds for latency and cost improvements
