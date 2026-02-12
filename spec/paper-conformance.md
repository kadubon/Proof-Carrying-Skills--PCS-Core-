# Conformance to "Stop Recomputing for AI/LLMs" (PCS-Core)

Last strict alignment review: **February 12, 2026**.

This document tracks repository conformance to the paper and highlights residual gaps.

## Scope of Check

- paper: *Stop Recomputing for AI/LLMs: Proof-Carrying Skills for Compute-Saving Inference Reuse*
- focus: OSS publication, specifications, deterministic verifier, vectors/compatibility, optional cost frame

## Conformance Matrix

| Layer | Paper expectation | Repository status |
|---|---|---|
| OSI-compliant OSS publication | Reproducible and contribution-friendly release | `LICENSE` (Apache-2.0), `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` present |
| Specification (`schema`, `rulebook`, `canonicalization`) | JSON Schema + semantic rulebook + deterministic canonicalization | `schemas/pcs-core-v1-bundle.schema.json`, `schemas/pcs-v1-extensions.schema.json`, `spec/rulebook.md`, `spec/canonicalization-qjcs-v1.md`, `spec/schema-profile.md` |
| Minimal deterministic verifier | strict parser, invocation/output binding, bounded OPVM, reject codes | `reference-checker/pcs_core.py` implements fail-closed deterministic checks and `decision_sha256` |
| PCS-Blob | domain-separated Merkle inclusion verification for event logs/chunks | Implemented for `included_events` and `included_chunks` in VTR path |
| GLUE composition | deterministic mapping + bridge assertions + invocation binding | Implemented via composition bundle (`from_bundle`,`to_bundle`,`glue_receipt`) |
| Test vectors / compatibility suite | replayable vectors with determinism check | `test-vectors/*` + `compatibility-suite/run_vectors.py` (bundle + schema modes) |
| Optional cost model frame (Section 12 intent) | explicit cost decomposition + amortization + reproducible reporting | Implemented as separate non-gating frame with command logs and environment fingerprint: `PoC/e6_optional_cost_runner.py`, `PoC/e6_optional_cost_config.example.json`, `PoC/experiment_master_plan.yaml` |

## Strict MUST Checklist

| Paper requirement (MUST-level intent) | Status | Evidence |
|---|---|---|
| Strict JSON parse (duplicate-key rejection, i64-only numbers) | PASS | `reference-checker/pcs_core.py` strict parser path |
| Deterministic invocation binding (`invocation_id`) | PASS | recomputation and equality checks in VTR and composition paths |
| Output commitment recomputation (json/bytes) | PASS | `out_json_qjcs_sha256` / `out_bytes_sha256` checks |
| Trace mode handling (inline OR Merkle OR none) | PASS | VTR receipt structural checks + evidence processor |
| Merkle inclusion verification with domain separation | PASS | `_merkle_leaf_hash`, `_merkle_node_hash`, `_verify_merkle_inclusion` |
| Blob chunk constraints and duplicate rejection | PASS | chunk index/size checks + `(blob_root,chunk_index)` uniqueness |
| Event assertions only on proven events | PASS | assertion evaluation against proven event map |
| GLUE mapping + bridge assertions under bounds | PASS | `validate_glue_receipt` + `_verify_glue_against_transition` |
| Deterministic compatibility replay | PASS | vector runner executes each case twice |

## Section-Level Mapping (Paper -> Implementation)

- Section 8.1 (domain-separated Merkle tree): implemented in `reference-checker/pcs_core.py` via `_merkle_leaf_hash` and `_merkle_node_hash`.
- Section 8.2 (inclusion proof verification): implemented via `_verify_merkle_inclusion` with direction-aware fold (`L`/`R`).
- Section 8.3 (blob chunks): implemented in `_process_vtr_evidence` for `included_chunks` with `(blob_root,chunk_index)` uniqueness and chunk-length constraints.
- Section 8.4 (event logs): implemented in `_process_vtr_evidence` for `event_root`/`event_count`/`included_events` checks.
- Section 9.1 (VTR profile and deterministic algorithm): implemented in `_verify_vtr_bundle_obj` including output binding, trace mode exclusivity, and assertion evaluation.
- Section 10.1-10.2 (GLUE composition): implemented in `verify_composition_obj` and `_verify_glue_against_transition` using verified `from_bundle` environment, deterministic mapping, and bridge assertions.
- Section 12 (cost model framing): operational measurement harness implemented as optional separate frame in `PoC/e6_optional_cost_runner.py`, with equation mapping, amortization reporting, claim-readiness criteria, and audit artifacts (`*_command_log.jsonl`, `*_environment_fingerprint.json`).

## Residual Gaps

- BRS and SRR runtime semantics are not implemented (schema slots are reserved, runtime behavior intentionally absent).
- E6 operational claims still require external wrapper command disclosure and workload publication; simulated mode is for pipeline sanity only.