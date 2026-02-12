# Compatibility Suite

Runs all vectors under `test-vectors/` and compares outputs with expected artifacts.
Each vector is executed twice and must produce identical results.

## Supported Modes

- `bundle` (default): runs `reference-checker/verifier.py` against `bundle.json`
  - includes VTR and GLUE composition bundle vectors
- `schema`: validates `instance.json` against schema path from `expected.json`

## Run

```powershell
python compatibility-suite/run_vectors.py
```

The runner exits non-zero if any vector fails.

## Dependency for Schema Mode

Schema vectors require `jsonschema` (Draft 2020-12 validator).