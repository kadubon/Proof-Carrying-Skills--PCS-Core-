# Proof-Carrying Skills (PCS-Core) Reference Implementation

**Stop Recomputing for AI/LLMs** in practice: this project implements a deterministic verification layer for **compute-saving inference reuse**.

Instead of recomputing expensive model inference end-to-end, a caller can accept outputs when a compact proof-carrying receipt verifies under explicit bounds.

## Vision

AI/LLM deployment is increasingly constrained by repeated inference cost (latency, energy, and money). PCS reframes acceptance as a deterministic verification problem:

- compute once with an untrusted provider
- verify many times with a small trusted checker
- accept only if contract + evidence satisfy fail-closed rules

This repository implements that path with a small trusted computing base and no-meta trust assumptions.

## What Is Unique

Compared with typical caching or heuristic reuse, PCS adds cryptographic and semantic guarantees:

- **No-meta trust boundary**: providers are untrusted; acceptance is checker-driven
- **Deterministic OPVM predicates**: bounded gas/size/depth, no unbounded execution
- **Replay-resistant invocation binding**: receipts bound to checker-issued invocation headers
- **Verifiable Trace Receipt (VTR)**: output commitments plus optional trace evidence
- **PCS-Blob support**: Merkle inclusion proofs for event logs and blob chunks
- **GLUE receipts for composition**: deterministic bridge checks across sequential skills

## Implemented Layers

- PCS-Core deterministic checks
- VTR verification (inline trace, Merkle trace, event assertions)
- PCS-Blob verification (`included_events`, `included_chunks`)
- GLUE composition verification (`from_bundle` + `glue_receipt` + `to_bundle`)

Out of scope in this repository:

- BRS/SRR runtime semantics (schema slots reserved)

## Repository Layout

- `spec/`: normative and conformance documents
- `schemas/`:
  - `pcs-core-v1-bundle.schema.json`
  - `pcs-v1-extensions.schema.json`
  - `pcs-v1-extensions-placeholder.schema.json` (legacy alias)
- `reference-checker/`: deterministic verifier implementation (Python)
- `test-vectors/`: accept/reject vectors for bundle and schema modes
- `compatibility-suite/`: deterministic replay runner (runs vectors twice)

## Quickstart

1. Verify a standard VTR bundle:

```powershell
python reference-checker/verifier.py --bundle test-vectors/accept/basic_echo/bundle.json
```

2. Verify a GLUE composition bundle:

```powershell
python reference-checker/verifier.py --bundle test-vectors/accept/glue_composition/bundle.json
```

3. Run full compatibility suite:

```powershell
python compatibility-suite/run_vectors.py
```

## Search Keywords

Proof-Carrying Skills, PCS-Core, Stop Recomputing for AI/LLMs, compute-saving inference reuse, deterministic verifier, Verifiable Trace Receipt, GLUE receipt, Merkle inclusion proof, no-meta trust.

## Citation

Takahashi, K. (2026). *Stop Recomputing for AI/LLMs: Proof-Carrying Skills for Compute-Saving Inference Reuse*. Zenodo. https://doi.org/10.5281/zenodo.18490939