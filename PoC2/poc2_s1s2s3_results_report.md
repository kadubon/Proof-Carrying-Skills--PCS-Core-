# PoC2 Multi-Stage Results Report (S1_PILOT, S2_PREPROD, S3_FULL_EVIDENCE)

- Generated (UTC): `2026-02-13T04:57:24Z`
- Run tags: `poc2_s1_pilot_run1, poc2_s1_pilot_run2, poc2_s1_pilot_run3, poc2_s2_preprod_run1, poc2_s3_full_evidence_run1`

## Per-Run Summary

| tag | hard_pass | latency mean | latency CI low | cost(ms) mean | cost(ms) CI low | cost(token) mean | cost(token) CI low |
|---|---:|---:|---:|---:|---:|---:|---:|
| poc2_s1_pilot_run1 | True | 0.7893 | 0.7147 | 0.6184 | 0.4778 | -5.2902 | -8.3407 |
| poc2_s1_pilot_run2 | True | 0.7293 | 0.5399 | 0.5650 | 0.3418 | -5.2902 | -8.3407 |
| poc2_s1_pilot_run3 | True | 0.7608 | 0.6659 | 0.6065 | 0.4669 | -5.2902 | -8.3407 |
| poc2_s2_preprod_run1 | True | 0.7478 | 0.6749 | 0.6125 | 0.5220 | -5.2118 | -6.4511 |
| poc2_s3_full_evidence_run1 | True | 0.7789 | 0.7592 | 0.6225 | 0.5946 | -5.1841 | -5.6708 |

## Combined Summary (All Runs)

- rows: `1368` (pass_rows: `1368`)
- latency improvement: mean `0.7734`, 95% CI low `0.7558`
- cost improvement (ms model): mean `0.6199`, 95% CI low `0.5948`
- cost improvement (token model): mean `-5.1832`, 95% CI low `-5.6044`
- failure_rate_estimated: `0.0`
- retry_avg_per_operation_estimated: `0.0022637238256932655`

## Threshold Analysis

- ms-model threshold min N_reuse: `3`
- all-model threshold min N_reuse (latency + ms + token): `None`

| N_reuse | latency CI low > 0 | cost(ms) CI low > 0 | cost(token) CI low > 0 |
|---:|---:|---:|---:|
| 1 | False | False | False |
| 3 | True | True | False |
| 10 | True | True | False |

## Threshold Stability By Stage

| stage_id | rows | pass_rows | ms threshold min N | all-model threshold min N |
|---|---:|---:|---:|---:|
| S1_PILOT | 108 | 108 | 3 | None |
| S2_PREPROD | 180 | 180 | 3 | None |
| S3_FULL_EVIDENCE | 1080 | 1080 | 3 | None |

## Claim Readout

- can_claim_ms_model_effective_in_scope: `True`
- can_claim_token_model_effective_in_scope: `False`
- scope: `{'stage_profile': 'MULTI_STAGE', 'stage_profiles_included': ['S1_PILOT', 'S2_PREPROD', 'S3_FULL_EVIDENCE'], 'workload_ids_included': ['W1_gemini_api'], 'complexity_tiers_included': ['heavy', 'light', 'medium'], 'concurrency_levels_included': [1, 4], 'reuse_counts_tested': [1, 3, 10], 'cost_scenarios_included': ['C1_equal_cost', 'C2_checker_advantage', 'C3_adverse_checker'], 'fixed_overheads_included': ['F0_zero', 'F1_small']}`

### Non-Claims
- No universal gain claim beyond tested stage profile and workload.
- No semantic quality improvement claim.
- Token-model gain is not claimed unless token CI lower bounds become positive.

## Scientific Honesty Notes
- All configured runs are included; no selective exclusion of negative regions.
- ABBA crossover and fixed prompt settings reduce but do not eliminate network/provider drift.
- Thresholds are conditional on the tested workload and cost assumptions.
