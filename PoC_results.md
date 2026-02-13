# PoC2 Operational Results Report (Engineer-Facing)

- Date (UTC): `2026-02-13`
- Repository: `Proof-Carrying Skills (PCS-Core)`
- LLM API/model used in this report: `gemini-2.5-flash-lite` via Gemini API
- Main evidence files: `PoC2/poc2_s2s3_results_report.json` (generated `2026-02-13T04:56:04Z`), `PoC2/poc2_s1s2s3_results_report.json` (generated `2026-02-13T04:57:24Z`)

## 1. Executive Summary

- `S2_PREPROD` and `S3_FULL_EVIDENCE` both passed all hard gates (`P1`, `P3`, `P4`).
- In this tested envelope, latency and ms-based cost improve when reuse density is sufficient.
- The empirically observed ms-model threshold is `N_reuse >= 3`, stable across S2 and S3.
- Token-based savings are not established in this evidence set.

Practical takeaway: PCS reuse is beneficial in this evidence set when reuse density is sufficient (`N_reuse >= 3`), while token-economics remain unresolved due to telemetry constraints; deployment should therefore be gated by reuse density and hard safety checks.

## 2. Scope and Runs Included

- `poc2_s2_preprod_run1` (`S2_PREPROD`)
- `poc2_s3_full_evidence_run1` (`S3_FULL_EVIDENCE`)

Both runs report:

- `P1_CONFORMANCE_SMOKE = pass`
- `P3_STRESS_FAIL_CLOSED = pass`
- `P4_DETERMINISM_SAMPLE = pass`
- `hard_pass = true`

## 3. Metric Definitions (Formal)

All improvement ratios are defined as:

- `latency_improvement_ratio = 1 - (pcs_latency / baseline_latency)`
- `cost_improvement_ratio_ms = 1 - (pcs_cost_ms / baseline_cost_ms)`
- `cost_improvement_ratio_token = 1 - (pcs_cost_token / baseline_cost_token)`

Interpretation:

- positive: PCS is better
- zero: no difference
- negative: PCS is worse

If a denominator is non-positive, the ratio is treated as undefined and excluded from inferential statistics. In the report generator, bootstrap samples with non-positive aggregate denominator are skipped.

## 4. Statistical Unit and Confidence Intervals

- Primary inferential unit: campaign-level aggregate row in `*_campaigns.csv` (not per-request rows).
- Confidence interval method in generated reports: bootstrap percentile.
- Resampling count used by `PoC2/generate_poc2_results_report.py`: `n_resamples = 5000`.
- Any request-level or raw-row views are diagnostic, not primary inferential evidence.

## 5. Hard-Gate Semantics

A run is operationally admissible only when all hard gates pass:

- `P1_CONFORMANCE_SMOKE`: checker/schema compatibility smoke validation
- `P3_STRESS_FAIL_CLOSED`: failure paths do not fail-open
- `P4_DETERMINISM_SAMPLE`: deterministic verification behavior under sampled replay

If any hard gate fails, cost/latency claims from that run are treated as non-actionable.

## 6. Quantitative Results (S2 + S3 Combined)

- rows: `1260` (`pass_rows = 1260`)
- latency improvement: mean `0.773993`, 95% CI `[0.754563, 0.790780]`
- cost improvement (ms model): mean `0.620851`, 95% CI `[0.593802, 0.645646]`
- cost improvement (token model): mean `-5.187144`, 95% CI `[-5.627192, -4.752554]`
- failure rate estimate: `0.0`
- retry avg per operation estimate: `0.002227`

## 7. Where PCS Helps and Where It Does Not

Threshold criterion: CI lower bound `> 0`.

| N_reuse | latency CI low | cost(ms) CI low | cost(token) CI low | Practical reading |
|---:|---:|---:|---:|---|
| 1 | -0.373959 | -1.088469 | -30.483472 | No measurable benefit; often worse than recomputation |
| 3 | 0.640299 | 0.390621 | -9.541154 | Clear latency and ms-cost gain starts here |
| 10 | 0.895985 | 0.819827 | -2.151708 | Strong latency and ms-cost gains |

Concrete scenario examples:

- Strong gain cases:
- `N_reuse = 10`, `C1_equal_cost`: latency CI low `0.893814`, ms-cost CI low `0.815425`
- `N_reuse = 10`, `C2_checker_advantage`: latency CI low `0.889530`, ms-cost CI low `0.809884`
- `N_reuse = 10`, `C3_adverse_checker`: latency CI low `0.892589`, ms-cost CI low `0.814132`
- Weak/no-gain cases:
- `N_reuse = 1`, `C1_equal_cost`: latency CI low `-0.164525`, ms-cost CI low `-0.946020`
- `N_reuse = 1`, `C2_checker_advantage`: latency CI low `-0.240394`, ms-cost CI low `-1.024641`
- `N_reuse = 1`, `C3_adverse_checker`: latency CI low `-0.884121`, ms-cost CI low `-1.636119`

## 8. Threshold Stability Across Stages

| stage_id | rows | ms threshold min N_reuse | all-model threshold (latency + ms + token) |
|---|---:|---:|---:|
| `S2_PREPROD` | 180 | 3 | None |
| `S3_FULL_EVIDENCE` | 1080 | 3 | None |

Interpretation: the ms-model threshold (`N_reuse = 3`) is stable across S2 and S3.

## 9. Deployment Decision Rule (Current Evidence Scope)

Within the tested scope only:

- enable PCS path when expected `N_reuse >= 3`
- prefer recomputation when expected `N_reuse = 1`
- keep token-cost as non-claim until telemetry completeness improves

This rule is model-conditional (`gemini-2.5-flash-lite`) and must be re-validated for other models.

## 10. Token-Usage Missingness Policy

- Current token-usage missing rate: `0.45161290322580644`.
- Token-economics are treated as conditional on telemetry completeness.
- Arm-level missingness (`baseline`, `pcs_run`, `pcs_verify`) should be monitored; the current report uses overall missingness and therefore keeps token-cost in non-claim status.
- If missingness is high or arm-asymmetric, token-cost remains exploratory and non-gating.

## 11. Failure and Retry Interpretation

- `failure_rate = 0.0` in this dataset means no observed hard failures in the tested envelope.
- This is not a universal reliability guarantee outside tested workload/concurrency envelope.
- `retry_avg_per_operation` is an operational pressure indicator, not a quality metric.

## 12. Cost Model Boundary

Two cost views are reported:

- ms-based model: infrastructure-time proxy
- token-based model: API-billing proxy

These answer different questions and can diverge. In this report, rollout decisions prioritize latency + ms-cost, where evidence is established. Token-cost remains a tracked risk item.

## 13. Model Scope and Transferability Risk

This report uses a single API/model (`gemini-2.5-flash-lite`). Numeric thresholds may differ across models because of:

- different latency profiles by model/service tier
- different tokenization and output-length behavior
- different pricing terms
- different availability/quality of token usage metadata
- different backend non-determinism characteristics

Therefore, thresholds here are model-conditional, not universal.

## 14. Explicit Non-Claims

This report does not claim:

- universal PCS gains outside tested workloads/settings/models
- model quality improvement
- token-cost savings under current telemetry completeness
- transferability of numeric thresholds without replication

## 15. Auditability Contract

- Each run is auditable via immutable fingerprints (`config_sha256`, `runner_sha256`, `gemini_driver_sha256`, `plan_sha256`) and per-call logs.
- Any post-hoc code/config change invalidates direct comparability unless re-run under the new fingerprint set.

Fingerprint table:

| run tag | config_sha256 | runner_sha256 | gemini_driver_sha256 | plan_sha256 |
|---|---|---|---|---|
| `poc2_s2_preprod_run1` | `9e7ccd64457240742667eedd58e621729437bff4b38b0e38dadc8141feab59bf` | `01cbce2d80945165e3ed3754c546870d28790840deff3a3e0480ba2a4bac7166` | `6976e030a3b33cdd4605154fda41a5c51c5abb162286cca3868b40b4a56110fe` | `3b18c5cd3e9dccb5f0cc7ca4a39e48c77b71d6fc62a631b99ff3e23d6f715db1` |
| `poc2_s3_full_evidence_run1` | `a7c1dfabb235d68d26feaff70e65a0b04be6026c33fe179b13883de259ec9f91` | `01cbce2d80945165e3ed3754c546870d28790840deff3a3e0480ba2a4bac7166` | `6976e030a3b33cdd4605154fda41a5c51c5abb162286cca3868b40b4a56110fe` | `3b18c5cd3e9dccb5f0cc7ca4a39e48c77b71d6fc62a631b99ff3e23d6f715db1` |

## 16. Promotion Criteria to S4 (Production Pilot)

Proceed to S4 only if all conditions hold:

1. hard gates remain pass across additional independent repetitions
2. campaign-level CI lower bound remains `> 0` for latency and ms-cost at target `N_reuse`
3. token-usage missingness is reduced to policy-acceptable level, or token-cost remains explicit non-claim
4. results replicate on at least one additional model family

## 17. Reproducibility Commands and Artifact Pointers

Re-run commands:

```powershell
python PoC2/poc2_operational_runner.py --config PoC2/staged-configs/poc2_s2_preprod.yaml --out-dir PoC2/runs --tag poc2_s2_preprod_run1
python PoC2/poc2_operational_runner.py --config PoC2/staged-configs/poc2_s3_full_evidence.yaml --out-dir PoC2/runs --tag poc2_s3_full_evidence_run1
python PoC2/generate_poc2_results_report.py --runs-dir PoC2/runs --tags poc2_s2_preprod_run1 poc2_s3_full_evidence_run1 --out-json PoC2/poc2_s2s3_results_report.json --out-md PoC2/poc2_s2s3_results_report.md
```

Key audit artifacts:

- `PoC2/runs/poc2_s2_preprod_run1_protocol_fingerprint.json`
- `PoC2/runs/poc2_s3_full_evidence_run1_protocol_fingerprint.json`
- `PoC2/runs/poc2_s2_preprod_run1_workload_manifest_sha256.json`
- `PoC2/runs/poc2_s3_full_evidence_run1_workload_manifest_sha256.json`
- `PoC2/runs/poc2_s2_preprod_run1_gemini_requests.jsonl`
- `PoC2/runs/poc2_s2_preprod_run1_gemini_responses.jsonl`
- `PoC2/runs/poc2_s2_preprod_run1_gemini_errors.jsonl`
- `PoC2/runs/poc2_s3_full_evidence_run1_gemini_requests.jsonl`
- `PoC2/runs/poc2_s3_full_evidence_run1_gemini_responses.jsonl`
- `PoC2/runs/poc2_s3_full_evidence_run1_gemini_errors.jsonl`
