# PoC2 S1 Pilot Results Report

- Generated (UTC): `2026-02-13T01:54:28Z`
- Run tags: `poc2_s1_pilot_run1, poc2_s1_pilot_run2, poc2_s1_pilot_run3`

## Per-Run Summary

| tag | hard_pass | latency mean | latency CI low | cost(ms) mean | cost(ms) CI low | cost(token) mean | cost(token) CI low |
|---|---:|---:|---:|---:|---:|---:|---:|
| poc2_s1_pilot_run1 | True | 0.7893 | 0.7147 | 0.6184 | 0.4778 | -5.2902 | -8.3407 |
| poc2_s1_pilot_run2 | True | 0.7293 | 0.5399 | 0.5650 | 0.3418 | -5.2902 | -8.3407 |
| poc2_s1_pilot_run3 | True | 0.7608 | 0.6659 | 0.6065 | 0.4669 | -5.2902 | -8.3407 |

## Combined Summary (All Runs)

- rows: `108` (pass_rows: `108`)
- latency improvement: mean `0.7612`, 95% CI low `0.6939`
- cost improvement (ms model): mean `0.6016`, 95% CI low `0.5154`
- cost improvement (token model): mean `-5.2082`, 95% CI low `-6.8072`
- failure_rate_estimated: `0.0`
- retry_avg_per_operation_estimated: `0.002688172043010753`

## Threshold Analysis

- ms-model threshold min N_reuse: `3`
- all-model threshold min N_reuse (latency + ms + token): `None`

| N_reuse | latency CI low > 0 | cost(ms) CI low > 0 | cost(token) CI low > 0 |
|---:|---:|---:|---:|
| 1 | False | False | False |
| 3 | True | True | False |
| 10 | True | True | False |

## Claim Readout

- can_claim_ms_model_effective_in_scope: `True`
- can_claim_token_model_effective_in_scope: `False`
- scope: `{'stage_profile': 'S1_PILOT', 'complexity_tiers_included': ['light'], 'concurrency_levels_included': [1], 'reuse_counts_tested': [1, 3, 10], 'cost_scenarios_included': ['C1_equal_cost', 'C2_checker_advantage'], 'fixed_overheads_included': ['F0_zero', 'F1_small']}`

### Non-Claims
- No universal gain claim beyond tested stage profile and workload.
- No semantic quality improvement claim.
- Token-model gain is not claimed unless token CI lower bounds become positive.

## Scientific Honesty Notes
- All configured runs are included; no selective exclusion of negative regions.
- ABBA crossover and fixed prompt settings reduce but do not eliminate network/provider drift.
- Thresholds are conditional on the tested workload and cost assumptions.
