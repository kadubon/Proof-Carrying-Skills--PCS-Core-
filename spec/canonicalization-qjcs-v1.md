# QJCS v1 Canonicalization (Minimal Profile)

## 1. Supported Value Types

- `null`
- `boolean`
- `string`
- signed 64-bit integer
- array
- object

Non-integer JSON numbers are forbidden.

## 2. Tagged Fixed-Point (`qnum`)

`qnum` is represented as:

```json
{"qnum":{"m":12345,"s":3}}
```

Rules:

- outer object MUST contain exactly one key: `qnum`
- inner object MUST contain exactly keys `m`, `s`
- `m` MUST be signed 64-bit integer
- `s` MUST be integer in `[0,18]`

## 3. Canonical Emission

- objects: keys sorted lexicographically by Unicode code point
- no insignificant whitespace
- booleans/literals use lowercase JSON forms
- integers emitted in decimal without leading `+`
- strings emitted as valid JSON string literals

## 4. Hashes and IDs

All digest strings use:

- lowercase hex
- prefix `sha256:`

Definitions (paper-aligned):

- input/output JSON commitment: `SHA256(QJCS(value))`
- raw bytes commitment: `SHA256(raw_bytes)`
- contract ID: `SHA256("PCS-CONTRACT" || QJCS(contract_without_contract_id))`
- invocation ID: `SHA256("PCS-INVOKE" || QJCS(invocation_header))`
