# Test Vectors

Golden vectors for deterministic acceptance/rejection behavior.

## Regenerate Bundle Vectors

```powershell
python test-vectors/generate_vectors.py
```

Generated checker decision cases (`mode: bundle`) include:

- accepts:
  - `accept/basic_echo`
  - `accept/basic_echo_bytes`
  - `accept/vtr_inline_trace_assert`
  - `accept/vtr_merkle_blob`
  - `accept/glue_composition`
- strict rejects (selected):
  - `reject/mixed_trace_modes`
  - `reject/assertion_unproven_event`
  - `reject/proof_len_exceeded`
  - `reject/max_inclusion_proofs_exceeded`
  - `reject/max_proven_bytes_exceeded`
  - `reject/chunk_nonfinal_not_full`
  - `reject/glue_duplicate_to_path`
  - `reject/glue_bounds_exceed_profile`
  - `reject/glue_to_path_invalid`
  - `reject/merkle_event_proof_invalid`

## Schema Compatibility Vectors

Hand-authored schema vectors (`mode: schema`) remain under:

- `schema/accept/*`
- `schema/reject/*`

Each schema vector contains:

- `instance.json`
- `expected.json` with `schema` reference and `valid` expectation