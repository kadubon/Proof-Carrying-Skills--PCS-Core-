# Reference Checker (Deterministic Verifier)

Minimal Python implementation of the PCS checker with deterministic fail-closed behavior.

## Implemented Guarantees

- strict JSON parsing (duplicate keys rejected)
- integer-only JSON numbers
- QJCS canonicalization + commitment checks
- deterministic invocation/contract binding
- VTR verification with:
  - inline trace events
  - Merkle trace verification (`event_root`, `included_events`)
  - blob chunk inclusion verification (`included_chunks`)
  - event-scoped assertions
- GLUE verification with:
  - deterministic mapping checks (`from` -> `/in/...`)
  - bridge predicate evaluation under bounded gas
  - composition binding to target invocation header
- deterministic decision fingerprint (`decision_sha256`)

## Run

```powershell
python reference-checker/verifier.py --bundle test-vectors/accept/basic_echo/bundle.json
```

```powershell
python reference-checker/verifier.py --bundle test-vectors/accept/glue_composition/bundle.json
```