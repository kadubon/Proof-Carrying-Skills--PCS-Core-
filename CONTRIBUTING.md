# Contributing

## Goals

Contributions should improve:

- determinism
- fail-closed safety
- replayability
- interoperability

## Ground Rules

- keep changes small and auditable
- update `spec/` when behavior changes
- update `schemas/` for wire-format changes
- add or update `test-vectors/` for every behavior change
- run the compatibility suite before submitting

## Local Checks

```powershell
python compatibility-suite/run_vectors.py
```

## Pull Request Checklist

- [ ] specification impact documented
- [ ] schema updated (if needed)
- [ ] new vectors added
- [ ] compatibility suite passes
- [ ] no non-deterministic behavior introduced
