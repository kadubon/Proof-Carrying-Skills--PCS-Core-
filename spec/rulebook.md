# PCS-Core Rulebook v1

## 1. Purpose

Define deterministic, fail-closed acceptance for reusable skill executions and composition.

## 2. Verification Inputs

### VTR bundle mode

A VTR bundle MUST contain:

- `contract`
- `invocation_header`
- `in`
- `out`
- `receipt`

For byte-output contracts, bundle MUST also include `out_bytes_b64`.

### Composition mode (GLUE)

A composition bundle MUST contain:

- `from_bundle`
- `to_bundle`
- `glue_receipt`

## 3. Deterministic Verification Order

### VTR mode

1. strict parse (duplicate-key rejection, integer-only numbers)
2. structural validation (`contract`, `invocation_header`, `receipt`)
3. deployment profile checks
4. `contract_id` recomputation and contract binding
5. `invocation_id` recomputation and invocation binding
6. output commitment verification (`out_json_qjcs_sha256` or `out_bytes_sha256`)
7. trace evidence processing:
   - inline OR Merkle OR none (mutually exclusive)
   - Merkle inclusion verification for `included_events`
8. blob chunk processing (`included_chunks`) with Merkle inclusion verification
9. bounds checks (`max_steps`, `max_inclusion_proofs`, `max_proven_bytes`, etc.)
10. predicate evaluation order:
   - precondition (`/out=null`, `/event=null`)
   - postcondition (`/out` set per I/O mode, `/event=null`)
   - assertions (`/event` set to referenced proven event)

### Composition mode

1. verify `from_bundle` (VTR)
2. validate and bound-check `glue_receipt`
3. verify GLUE mapping equality from proven `from_bundle` env to `to_bundle` `/in`
4. verify GLUE bridge assertions on proven `from_bundle` env (`/event=null`)
5. verify `to_bundle` (VTR)

## 4. Fail-Closed Decision

- `ACCEPT` only when all checks pass.
- otherwise `REJECT` with machine-readable code.

## 5. Reject Code Set (v1)

- `PARSE_ERROR`
- `SCHEMA_ERROR`
- `VERSION_UNSUPPORTED`
- `CONTRACT_INVALID`
- `INVOCATION_MISMATCH`
- `OUTPUT_MISMATCH`
- `BASE64_INVALID`
- `GAS_EXHAUSTED`
- `TYPE_ERROR`
- `BOUND_VIOLATION`
- `PRECOND_FAIL`
- `POSTCOND_FAIL`
- `ASSERT_FAIL`
- `PROOF_INVALID`

## 6. Determinism Rules

- same bytes + same checker build + same profile => same output artifact
- no random branch in verifier logic
- no network calls during verification
- no dynamic code execution
- include deterministic decision artifact hash (`decision_sha256`) in outputs