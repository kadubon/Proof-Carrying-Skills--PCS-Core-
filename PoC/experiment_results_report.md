# Experiment Results Report

- Generated (UTC): `2026-02-12T23:20:07Z`
- Protocol version: `2.2.0`

## Core Experiments (E1-E5)

| Experiment | Result | Evidence |
|---|---|---|
| E1_CONFORMANCE | PASS | total=40, failed=0 |
| E2_DETERMINISM | PASS | compatibility-suite executes each vector twice |
| E3_FAIL_CLOSED | PASS | reject_bundle_pass_count=28 |
| E4_BOUNDARY | PASS | missing_cases=[] |
| E5_SCHEMA_INTEROP | PASS | schema_pass_count=7 |

## Optional Experiment (E6_OPTIONAL_COST)

- failed_scenarios: `[]`

### Scenario `operational_wrapper_pilot_v1`
- mode: `command`
- status: `ok`
- campaigns_completed: `30` / `30`
- episodes_per_campaign: `100`
- supports_operational_claim: `True`
- latency_improvement_ratio_mean: `0.19524001510606973`
- latency_improvement_ratio_ci95: `[0.19287664363424126, 0.1974266750661866]`
- cost_improvement_ratio_mean: `0.3885002811287983`
- cost_improvement_ratio_ci95: `[0.3865142713780337, 0.39110714757760356]`

#### Claim Criteria
- cost_ci_lower_positive: `True`
- latency_ci_lower_positive: `True`
- minimum_campaigns_met: `True`
- minimum_episodes_met: `True`
- mode_is_command: `True`
- scenario_status_ok: `True`
- workload_disclosure_complete: `True`

## Operational Claim

- ready: `True`
- statement: Operational claim is supported for the disclosed command-mode workload and measured environment.

### Scope Limitations
- Applies to the disclosed workload and harness configuration only.
- Does not imply universal improvement across all production models/workloads.
- PCS-Core correctness/safety claims remain independently gated by E1-E5.

## Scientific Integrity Notes
- All executed runs are retained in artifacts (including command logs).
- E6 is reported as optional/non-gating and separated from PCS-Core correctness claims.
- Interpretation is limited to measured evidence and declared workload scope.

## Artifacts
- e1_log: `PoC/runs/e1_conformance.log`
- e1_summary: `PoC/runs/e1_summary.json`
- e6_report_json: `PoC/runs/e6_optional_cost_report.json`
- e6_report_md: `PoC/runs/e6_optional_cost_report.md`
- e6_campaign_csv: `PoC/runs/e6_optional_cost_campaigns.csv`
- e6_command_log: `PoC/runs/e6_optional_cost_command_log.jsonl`
- e6_environment: `PoC/runs/e6_optional_cost_environment_fingerprint.json`
- workload_disclosure: `PoC/workloads/prod_workload_v1.md`
- workload_manifest: `PoC/workloads/operational_input_manifest.json`