# Security Policy

## Supported Scope

Security reports are accepted for:

- `reference-checker/`
- `schemas/`
- `compatibility-suite/`
- `test-vectors/` integrity issues

## Reporting a Vulnerability

Please open a private security report through your preferred secure channel with:

- impact summary
- reproduction steps
- affected files/commit
- suggested fix (optional)

If private reporting is unavailable, open a public issue with minimal exploit details and mark it as security-sensitive.

## Response Targets

- initial triage: within 7 days
- remediation plan: within 21 days
- coordinated disclosure after a fix is available

## Disclosure Principles

- fail-closed behavior has priority over convenience
- deterministic replay integrity must not regress
- schema and verifier changes require new or updated test vectors
