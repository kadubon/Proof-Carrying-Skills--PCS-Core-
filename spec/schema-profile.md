# Schema Profile (Draft 2020-12)

## Scope

`schemas/pcs-core-v1-bundle.schema.json` covers implemented PCS-Core VTR wire objects:

- skill contract
- invocation header
- bundle envelope
- VTR receipts (inline trace, Merkle trace, included chunks, assertions)

`schemas/pcs-v1-extensions.schema.json` covers extension-layer objects used by this repository (legacy alias: `pcs-v1-extensions-placeholder.schema.json`):

- GLUE receipts (`mapping`, `bridge_assertions`, `bounds`)
- VTR Blob/Merkle evidence objects
- composition bundle envelope (`from_bundle`, `to_bundle`, `glue_receipt`)

## Enforcement Split

Schema enforces:

- structural shape and required fields
- discriminators (`receipt_type`, `pcs_version`)
- unknown-key rejection for covered objects
- profile-critical bound field presence

Verifier enforces semantic checks not expressible in schema:

- duplicate-key rejection at parse time
- canonical base64 round-trip checks
- `contract_id` recomputation
- `invocation_id` recomputation
- output commitment recomputation
- Merkle proof verification with domain separation and leaf-count binding
- glue mapping equality checks and bridge predicate execution
- gas/node/value/depth metering and deployment-profile caps