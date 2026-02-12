
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

I64_MIN = -(2**63)
I64_MAX = 2**63 - 1
U64_MAX = 2**64 - 1

DOMAIN_CONTRACT = b"PCS-CONTRACT"
DOMAIN_INVOKE = b"PCS-INVOKE"
DOMAIN_DECISION = b"PCS-DECISION"

MERKLE_LEAF_PREFIX = b"PCS-LEAF"
MERKLE_NODE_PREFIX = b"PCS-NODE"
MERKLE_DOMAIN_BLOB = "PCS-BLOB"
MERKLE_DOMAIN_EVENTLOG = "PCS-EVENTLOG"

CHECKER_VERSION = "pcs-core-min-verifier/0.5.0"

PARSE_MAX_DEPTH = 128
MAX_BUNDLE_BYTES = 2 * 1024 * 1024

# Fixed local deployment profile for a deterministic checker.
DEPLOYMENT_PROFILE = {
    "max_contract_bytes": 128 * 1024,
    "max_invocation_header_bytes": 16 * 1024,
    "max_receipt_bytes": 512 * 1024,
    "max_glue_receipt_bytes": 512 * 1024,
    "max_input_bytes": 256 * 1024,
    "max_output_bytes": 256 * 1024,
    "max_depth": 64,
    "max_ast_nodes": 2_000,
    "max_value_bytes": 64 * 1024,
    "max_gas_predicate": 5_000_000,
    "max_predicates": 256,
    "max_inclusion_proofs": 2_048,
    "max_proof_len": 256,
    "max_steps": 4_096,
    "max_chunk_bytes": 256 * 1024,
    "max_proven_bytes": 8 * 1024 * 1024,
    "max_glue_mappings": 1_024,
}

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
POINTER_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class VerifyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _ensure(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise VerifyError(code, message)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_pos_int(value: Any) -> bool:
    return _is_int(value) and value > 0


def _is_nonneg_int(value: Any) -> bool:
    return _is_int(value) and value >= 0


def _check_i64(value: int, code: str = "SCHEMA_ERROR") -> None:
    _ensure(I64_MIN <= value <= I64_MAX, code, f"integer out of i64 range: {value}")


def _strict_parse_int(token: str) -> int:
    value = int(token)
    _check_i64(value, "PARSE_ERROR")
    return value


def _strict_parse_float(_token: str) -> float:
    raise ValueError("non-integer JSON number is forbidden")


def _strict_parse_constant(token: str) -> Any:
    raise ValueError(f"invalid JSON constant: {token}")


def _strict_object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key: {key}")
        obj[key] = value
    return obj


def parse_strict_json_bytes(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerifyError("PARSE_ERROR", f"input is not utf-8: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object_pairs_hook,
            parse_int=_strict_parse_int,
            parse_float=_strict_parse_float,
            parse_constant=_strict_parse_constant,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise VerifyError("PARSE_ERROR", str(exc)) from exc


def _validate_qnum_shape(value: Any, path: str) -> None:
    _ensure(isinstance(value, dict), "CANON_ERROR", f"{path}: qnum must be object")
    _ensure(set(value.keys()) == {"m", "s"}, "CANON_ERROR", f"{path}: qnum keys must be m,s")
    m = value["m"]
    s = value["s"]
    _ensure(_is_int(m), "CANON_ERROR", f"{path}: qnum.m must be integer")
    _ensure(_is_int(s), "CANON_ERROR", f"{path}: qnum.s must be integer")
    _check_i64(m, "CANON_ERROR")
    _ensure(0 <= s <= 18, "CANON_ERROR", f"{path}: qnum.s out of range")


def validate_qjcs_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, bool) or isinstance(value, str):
        return
    if _is_int(value):
        _check_i64(value, "CANON_ERROR")
        return
    if isinstance(value, list):
        for idx, item in enumerate(value):
            validate_qjcs_value(item, f"{path}[{idx}]")
        return
    if isinstance(value, dict):
        for key in value.keys():
            _ensure(isinstance(key, str), "CANON_ERROR", f"{path}: object key must be string")
        if "qnum" in value:
            _ensure(len(value) == 1, "CANON_ERROR", f"{path}: qnum tag object must be single-key")
            _validate_qnum_shape(value["qnum"], f"{path}.qnum")
            return
        for key, sub in value.items():
            validate_qjcs_value(sub, f"{path}.{key}")
        return
    raise VerifyError("CANON_ERROR", f"{path}: unsupported type: {type(value).__name__}")


def validate_qjcs_object(value: Any, context: str) -> None:
    _ensure(isinstance(value, dict), "SCHEMA_ERROR", f"{context} must be object")
    _ensure(not ("qnum" in value and len(value) == 1), "SCHEMA_ERROR", f"{context} must be a JSON object")
    validate_qjcs_value(value)


def _emit_qjcs(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if _is_int(value):
        _check_i64(value, "CANON_ERROR")
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_emit_qjcs(v) for v in value) + "]"
    if isinstance(value, dict):
        parts: list[str] = []
        for key in sorted(value.keys()):
            key_json = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
            parts.append(f"{key_json}:{_emit_qjcs(value[key])}")
        return "{" + ",".join(parts) + "}"
    raise VerifyError("CANON_ERROR", f"unsupported type: {type(value).__name__}")


def qjcs_canonical_bytes(value: Any) -> bytes:
    validate_qjcs_value(value)
    return _emit_qjcs(value).encode("utf-8")


def _sha256_prefixed(payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    return f"sha256:{digest}"


def _sha256_prefixed_domain(domain: bytes, payload: bytes) -> str:
    digest = hashlib.sha256(domain + payload).hexdigest()
    return f"sha256:{digest}"


def _sha256_raw(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def _sha256_bytes_from_prefixed(value: str, context: str) -> bytes:
    _ensure(isinstance(value, str), "SCHEMA_ERROR", f"{context} must be string")
    _ensure(SHA256_RE.match(value) is not None, "SCHEMA_ERROR", f"{context} must match sha256 format")
    return bytes.fromhex(value.split(":", 1)[1])


def qjcs_hash(value: Any) -> str:
    # Paper-conformant commitment: SHA-256 of QJCS bytes (no extra domain separator).
    return _sha256_prefixed(qjcs_canonical_bytes(value))


def raw_bytes_hash(value: bytes) -> str:
    return _sha256_prefixed(value)


def recompute_contract_id(contract: dict[str, Any]) -> str:
    body = copy.deepcopy(contract)
    body.pop("contract_id", None)
    return _sha256_prefixed_domain(DOMAIN_CONTRACT, qjcs_canonical_bytes(body))


def recompute_invocation_id(invocation_header: dict[str, Any]) -> str:
    return _sha256_prefixed_domain(DOMAIN_INVOKE, qjcs_canonical_bytes(invocation_header))

def _iter_json_nodes(root: Any) -> list[tuple[str, Any, int]]:
    nodes: list[tuple[str, Any, int]] = []
    stack: list[tuple[str, Any, int]] = [("$", root, 1)]
    while stack:
        path, value, depth = stack.pop()
        nodes.append((path, value, depth))
        if isinstance(value, dict):
            for key, child in value.items():
                stack.append((f"{path}.{key}", child, depth + 1))
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                stack.append((f"{path}[{idx}]", child, depth + 1))
    return nodes


def _enforce_value_bounds(value: Any, max_depth: int, max_value_bytes: int, context: str) -> None:
    for path, node, depth in _iter_json_nodes(value):
        if depth > max_depth:
            raise VerifyError("BOUND_VIOLATION", f"{context}{path}: max_depth exceeded")
        qbytes = len(qjcs_canonical_bytes(node))
        if qbytes > max_value_bytes:
            raise VerifyError(
                "BOUND_VIOLATION",
                f"{context}{path}: max_value_bytes exceeded ({qbytes}>{max_value_bytes})",
            )


def _compute_depth(value: Any) -> int:
    max_depth = 0
    for _path, _node, depth in _iter_json_nodes(value):
        if depth > max_depth:
            max_depth = depth
    return max_depth


def _decision_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "checker_version": result.get("checker_version"),
        "decision": result.get("decision"),
        "rejection_code": result.get("rejection_code"),
        "contract_id": result.get("contract_id"),
        "invocation_id": result.get("invocation_id"),
        "gas_used": result.get("gas_used"),
    }


def _decision_hash(result: dict[str, Any]) -> str:
    payload = _decision_payload(result)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_prefixed_domain(DOMAIN_DECISION, serialized)


def _attach_decision_hash(result: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(result)
    out["decision_sha256"] = _decision_hash(out)
    return out


def _validate_sha256_string(value: Any, context: str) -> None:
    _ensure(isinstance(value, str), "SCHEMA_ERROR", f"{context} must be string")
    _ensure(SHA256_RE.match(value) is not None, "SCHEMA_ERROR", f"{context} must match sha256 format")


def _validate_b64_canonical(value: Any, context: str) -> None:
    _ensure(isinstance(value, str), "BASE64_INVALID", f"{context} must be string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise VerifyError("BASE64_INVALID", f"{context} invalid base64: {exc}") from exc
    canon = base64.b64encode(decoded).decode("ascii")
    _ensure(canon == value, "BASE64_INVALID", f"{context} non-canonical base64")


def _validate_b64_suffix_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, sub in value.items():
            next_path = f"{path}.{key}"
            if key.endswith("_b64"):
                _validate_b64_canonical(sub, next_path)
            _validate_b64_suffix_fields(sub, next_path)
    elif isinstance(value, list):
        for idx, sub in enumerate(value):
            _validate_b64_suffix_fields(sub, f"{path}[{idx}]")


def _check_keys(obj: Any, required: set[str], optional: set[str], context: str) -> None:
    _ensure(isinstance(obj, dict), "SCHEMA_ERROR", f"{context} must be object")
    keys = set(obj.keys())
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    _ensure(not missing, "SCHEMA_ERROR", f"{context} missing keys: {missing}")
    _ensure(not unknown, "SCHEMA_ERROR", f"{context} unknown keys: {unknown}")


def _validate_restricted_pointer(pointer: Any, context: str) -> None:
    _ensure(isinstance(pointer, str), "SCHEMA_ERROR", f"{context} must be string")
    _ensure(pointer.startswith("/"), "SCHEMA_ERROR", f"{context} must start with '/'")
    segments = pointer.split("/")[1:]
    _ensure(len(segments) >= 1, "SCHEMA_ERROR", f"{context} must contain at least one segment")
    for raw_segment in segments:
        _ensure(raw_segment != "", "SCHEMA_ERROR", f"{context}: pointer segment must not be empty")
        _ensure(
            POINTER_SEGMENT_RE.match(raw_segment) is not None,
            "SCHEMA_ERROR",
            f"{context}: invalid pointer segment: {raw_segment}",
        )


def _validate_to_in_pointer(pointer: Any, context: str) -> None:
    _validate_restricted_pointer(pointer, context)
    _ensure(pointer.startswith("/in/"), "SCHEMA_ERROR", f"{context} must start with '/in/'")


def _validate_get_pointer_syntax(pointer: str) -> None:
    _validate_restricted_pointer(pointer, "get pointer")


def _validate_opvm_ast(expr: Any, max_nodes: int) -> None:
    count = 0
    stack: list[Any] = [expr]
    while stack:
        node = stack.pop()
        count += 1
        if count > max_nodes:
            raise VerifyError("BOUND_VIOLATION", "max_ast_nodes exceeded")

        _ensure(isinstance(node, dict), "SCHEMA_ERROR", "expression must be object")
        _ensure(len(node) == 1, "SCHEMA_ERROR", "expression must have one operator key")
        op, arg = next(iter(node.items()))

        if op == "const":
            validate_qjcs_value(arg)
            continue
        if op == "get":
            _validate_get_pointer_syntax(arg)
            continue
        if op in {"eq", "ne", "gt", "ge", "lt", "le", "add", "sub", "mul", "in"}:
            _ensure(isinstance(arg, list) and len(arg) == 2, "SCHEMA_ERROR", f"{op} expects 2 args")
            stack.append(arg[1])
            stack.append(arg[0])
            continue
        if op == "not":
            stack.append(arg)
            continue
        if op in {"and", "or"}:
            _ensure(isinstance(arg, list) and len(arg) >= 1, "SCHEMA_ERROR", f"{op} expects list")
            for item in reversed(arg):
                stack.append(item)
            continue
        raise VerifyError("SCHEMA_ERROR", f"unsupported operator: {op}")


def validate_contract(contract: Any) -> None:
    required = {
        "pcs_version",
        "contract_id",
        "skill_id",
        "version",
        "ttl_seconds",
        "io",
        "predicate_vm",
        "canonicalization",
        "precondition",
        "postcondition",
        "resource_bounds",
        "receipt_rule",
    }
    optional = {"coverage_policy", "failure_modes", "extensions"}
    _check_keys(contract, required, optional, "contract")

    _ensure(contract["pcs_version"] == "v1", "VERSION_UNSUPPORTED", "contract.pcs_version must be v1")
    _validate_sha256_string(contract["contract_id"], "contract.contract_id")
    _ensure(isinstance(contract["skill_id"], str) and contract["skill_id"], "SCHEMA_ERROR", "skill_id invalid")
    _ensure(isinstance(contract["version"], str) and contract["version"], "SCHEMA_ERROR", "version invalid")
    _ensure(_is_pos_int(contract["ttl_seconds"]), "SCHEMA_ERROR", "ttl_seconds must be positive integer")

    io = contract["io"]
    _check_keys(io, {"input", "output"}, set(), "contract.io")
    input_io = io["input"]
    output_io = io["output"]
    _check_keys(input_io, {"mode", "max_bytes"}, set(), "contract.io.input")
    _check_keys(output_io, {"mode", "max_bytes"}, set(), "contract.io.output")
    _ensure(input_io["mode"] == "json", "SCHEMA_ERROR", "input mode must be json")
    _ensure(output_io["mode"] in {"json", "bytes"}, "SCHEMA_ERROR", "output mode must be json or bytes")
    _ensure(_is_pos_int(input_io["max_bytes"]), "SCHEMA_ERROR", "input max_bytes invalid")
    _ensure(_is_pos_int(output_io["max_bytes"]), "SCHEMA_ERROR", "output max_bytes invalid")

    pvm = contract["predicate_vm"]
    _check_keys(
        pvm,
        {"name", "gas_predicate", "gas_per_byte", "max_depth", "max_ast_nodes", "max_value_bytes", "int_overflow"},
        set(),
        "contract.predicate_vm",
    )
    _ensure(pvm["name"] == "opvm-v1", "VERSION_UNSUPPORTED", "predicate_vm.name must be opvm-v1")
    _ensure(_is_pos_int(pvm["gas_predicate"]), "SCHEMA_ERROR", "gas_predicate must be positive integer")
    _ensure(_is_pos_int(pvm["gas_per_byte"]), "SCHEMA_ERROR", "gas_per_byte must be positive integer")
    _ensure(_is_pos_int(pvm["max_depth"]), "SCHEMA_ERROR", "max_depth must be positive integer")
    _ensure(_is_pos_int(pvm["max_ast_nodes"]), "SCHEMA_ERROR", "max_ast_nodes must be positive integer")
    _ensure(_is_pos_int(pvm["max_value_bytes"]), "SCHEMA_ERROR", "max_value_bytes must be positive integer")
    _ensure(pvm["int_overflow"] in {"reject", "saturate"}, "SCHEMA_ERROR", "int_overflow invalid")

    canonicalization = contract["canonicalization"]
    _check_keys(
        canonicalization,
        {"name", "default_scale10", "tensor_chunk_bytes", "max_scale10"},
        set(),
        "contract.canonicalization",
    )
    _ensure(canonicalization["name"] == "qjcs-v1", "VERSION_UNSUPPORTED", "canonicalization.name must be qjcs-v1")
    _ensure(_is_int(canonicalization["default_scale10"]), "SCHEMA_ERROR", "default_scale10 must be integer")
    _ensure(_is_int(canonicalization["max_scale10"]), "SCHEMA_ERROR", "max_scale10 must be integer")
    _ensure(0 <= canonicalization["default_scale10"] <= 18, "SCHEMA_ERROR", "default_scale10 out of range")
    _ensure(0 <= canonicalization["max_scale10"] <= 18, "SCHEMA_ERROR", "max_scale10 out of range")
    _ensure(_is_pos_int(canonicalization["tensor_chunk_bytes"]), "SCHEMA_ERROR", "tensor_chunk_bytes invalid")

    _check_keys(contract["precondition"], {"opvm"}, set(), "contract.precondition")
    _check_keys(contract["postcondition"], {"opvm"}, set(), "contract.postcondition")

    bounds = contract["resource_bounds"]
    _check_keys(
        bounds,
        {
            "max_receipt_bytes",
            "max_inclusion_proofs",
            "max_proof_len",
            "max_predicates",
            "max_steps",
            "max_chunk_bytes",
            "max_proven_bytes",
        },
        set(),
        "contract.resource_bounds",
    )
    _ensure(_is_pos_int(bounds["max_receipt_bytes"]), "SCHEMA_ERROR", "max_receipt_bytes invalid")
    _ensure(_is_nonneg_int(bounds["max_inclusion_proofs"]), "SCHEMA_ERROR", "max_inclusion_proofs invalid")
    _ensure(_is_nonneg_int(bounds["max_proof_len"]), "SCHEMA_ERROR", "max_proof_len invalid")
    _ensure(_is_pos_int(bounds["max_predicates"]), "SCHEMA_ERROR", "max_predicates invalid")
    _ensure(_is_nonneg_int(bounds["max_steps"]), "SCHEMA_ERROR", "max_steps invalid")
    _ensure(_is_pos_int(bounds["max_chunk_bytes"]), "SCHEMA_ERROR", "max_chunk_bytes invalid")
    _ensure(_is_nonneg_int(bounds["max_proven_bytes"]), "SCHEMA_ERROR", "max_proven_bytes invalid")

    rule = contract["receipt_rule"]
    _check_keys(rule, {"allowed_profiles", "profile_params"}, set(), "contract.receipt_rule")
    allowed_profiles = rule["allowed_profiles"]
    _ensure(
        isinstance(allowed_profiles, list) and len(allowed_profiles) >= 1,
        "SCHEMA_ERROR",
        "allowed_profiles must be a non-empty list",
    )
    _ensure(len(allowed_profiles) == len(set(allowed_profiles)), "SCHEMA_ERROR", "allowed_profiles must be unique")
    for idx, profile in enumerate(allowed_profiles):
        _ensure(profile in {"VTR", "BRS", "SRR"}, "SCHEMA_ERROR", f"allowed_profiles[{idx}] invalid")
    _ensure("VTR" in allowed_profiles, "SCHEMA_ERROR", "allowed_profiles must include VTR")

    profile_params = rule["profile_params"]
    _ensure(isinstance(profile_params, dict), "SCHEMA_ERROR", "profile_params must be object")
    _ensure("VTR" in profile_params, "SCHEMA_ERROR", "profile_params.VTR is required")
    vtr = profile_params["VTR"]
    _check_keys(vtr, {"max_assertions"}, set(), "contract.receipt_rule.profile_params.VTR")
    _ensure(_is_nonneg_int(vtr["max_assertions"]), "SCHEMA_ERROR", "profile_params.VTR.max_assertions invalid")
    for optional_profile in ("BRS", "SRR"):
        if optional_profile in profile_params:
            _ensure(
                isinstance(profile_params[optional_profile], dict),
                "SCHEMA_ERROR",
                f"profile_params.{optional_profile} must be object",
            )


def _validate_contract_against_profile(contract: dict[str, Any]) -> None:
    contract_bytes = len(qjcs_canonical_bytes(contract))
    _ensure(
        contract_bytes <= DEPLOYMENT_PROFILE["max_contract_bytes"],
        "CONTRACT_INVALID",
        "contract bytes exceed deployment profile",
    )

    pvm = contract["predicate_vm"]
    bounds = contract["resource_bounds"]
    io = contract["io"]
    canon = contract["canonicalization"]

    _ensure(pvm["max_depth"] <= DEPLOYMENT_PROFILE["max_depth"], "CONTRACT_INVALID", "predicate_vm.max_depth exceeds deployment profile")
    _ensure(pvm["max_ast_nodes"] <= DEPLOYMENT_PROFILE["max_ast_nodes"], "CONTRACT_INVALID", "predicate_vm.max_ast_nodes exceeds deployment profile")
    _ensure(pvm["max_value_bytes"] <= DEPLOYMENT_PROFILE["max_value_bytes"], "CONTRACT_INVALID", "predicate_vm.max_value_bytes exceeds deployment profile")
    _ensure(pvm["gas_predicate"] <= DEPLOYMENT_PROFILE["max_gas_predicate"], "CONTRACT_INVALID", "predicate_vm.gas_predicate exceeds deployment profile")

    _ensure(bounds["max_receipt_bytes"] <= DEPLOYMENT_PROFILE["max_receipt_bytes"], "CONTRACT_INVALID", "resource_bounds.max_receipt_bytes exceeds deployment profile")
    _ensure(bounds["max_predicates"] <= DEPLOYMENT_PROFILE["max_predicates"], "CONTRACT_INVALID", "resource_bounds.max_predicates exceeds deployment profile")
    _ensure(bounds["max_inclusion_proofs"] <= DEPLOYMENT_PROFILE["max_inclusion_proofs"], "CONTRACT_INVALID", "resource_bounds.max_inclusion_proofs exceeds deployment profile")
    _ensure(bounds["max_proof_len"] <= DEPLOYMENT_PROFILE["max_proof_len"], "CONTRACT_INVALID", "resource_bounds.max_proof_len exceeds deployment profile")
    _ensure(bounds["max_steps"] <= DEPLOYMENT_PROFILE["max_steps"], "CONTRACT_INVALID", "resource_bounds.max_steps exceeds deployment profile")
    _ensure(bounds["max_chunk_bytes"] <= DEPLOYMENT_PROFILE["max_chunk_bytes"], "CONTRACT_INVALID", "resource_bounds.max_chunk_bytes exceeds deployment profile")
    _ensure(bounds["max_proven_bytes"] <= DEPLOYMENT_PROFILE["max_proven_bytes"], "CONTRACT_INVALID", "resource_bounds.max_proven_bytes exceeds deployment profile")

    _ensure(canon["tensor_chunk_bytes"] <= bounds["max_chunk_bytes"], "CONTRACT_INVALID", "canonicalization.tensor_chunk_bytes exceeds resource_bounds.max_chunk_bytes")

    _ensure(io["input"]["max_bytes"] <= DEPLOYMENT_PROFILE["max_input_bytes"], "CONTRACT_INVALID", "io.input.max_bytes exceeds deployment profile")
    _ensure(io["output"]["max_bytes"] <= DEPLOYMENT_PROFILE["max_output_bytes"], "CONTRACT_INVALID", "io.output.max_bytes exceeds deployment profile")

    _validate_opvm_ast(contract["precondition"]["opvm"], pvm["max_ast_nodes"])
    _validate_opvm_ast(contract["postcondition"]["opvm"], pvm["max_ast_nodes"])


def validate_invocation_header(header: Any) -> None:
    _ensure(isinstance(header, dict), "SCHEMA_ERROR", "invocation_header must be object")
    required = {"pcs_version", "contract_id", "input_json_qjcs_sha256", "nonce_b64"}
    missing = sorted(required - set(header.keys()))
    _ensure(not missing, "SCHEMA_ERROR", f"invocation_header missing keys: {missing}")
    _ensure(header["pcs_version"] == "v1", "VERSION_UNSUPPORTED", "invocation_header.pcs_version must be v1")
    _validate_sha256_string(header["contract_id"], "invocation_header.contract_id")
    _validate_sha256_string(header["input_json_qjcs_sha256"], "invocation_header.input_json_qjcs_sha256")
    _validate_b64_canonical(header["nonce_b64"], "invocation_header.nonce_b64")


def _validate_merkle_proof_structure(proof: Any, context: str) -> None:
    _ensure(isinstance(proof, list), "SCHEMA_ERROR", f"{context} must be array")
    for idx, item in enumerate(proof):
        _check_keys(item, {"dir", "hash"}, set(), f"{context}[{idx}]")
        _ensure(item["dir"] in {"L", "R"}, "SCHEMA_ERROR", f"{context}[{idx}].dir invalid")
        _validate_sha256_string(item["hash"], f"{context}[{idx}].hash")

def validate_vtr_receipt(receipt: Any) -> None:
    required = {"pcs_version", "receipt_type", "contract_id", "skill_id", "invocation_id"}
    optional = {
        "out_json_qjcs_sha256",
        "out_bytes_sha256",
        "events",
        "event_root",
        "event_count",
        "included_events",
        "included_chunks",
        "assertions",
        "extensions",
    }
    _check_keys(receipt, required, optional, "receipt")
    _ensure(receipt["pcs_version"] == "v1", "VERSION_UNSUPPORTED", "receipt.pcs_version must be v1")
    _ensure(receipt["receipt_type"] == "VTR", "SCHEMA_ERROR", "receipt_type must be VTR")
    _validate_sha256_string(receipt["contract_id"], "receipt.contract_id")
    _ensure(isinstance(receipt["skill_id"], str) and receipt["skill_id"], "SCHEMA_ERROR", "receipt.skill_id invalid")
    _validate_sha256_string(receipt["invocation_id"], "receipt.invocation_id")

    has_json = "out_json_qjcs_sha256" in receipt
    has_bytes = "out_bytes_sha256" in receipt
    _ensure(has_json != has_bytes, "SCHEMA_ERROR", "receipt must contain exactly one output commitment")
    if has_json:
        _validate_sha256_string(receipt["out_json_qjcs_sha256"], "receipt.out_json_qjcs_sha256")
    if has_bytes:
        _validate_sha256_string(receipt["out_bytes_sha256"], "receipt.out_bytes_sha256")

    has_inline = "events" in receipt
    has_merkle_fields = any(k in receipt for k in ("event_root", "event_count", "included_events"))
    if has_inline and has_merkle_fields:
        raise VerifyError("SCHEMA_ERROR", "receipt cannot mix inline and merkle trace fields")
    if has_merkle_fields:
        _ensure(
            all(k in receipt for k in ("event_root", "event_count", "included_events")),
            "SCHEMA_ERROR",
            "merkle trace requires event_root,event_count,included_events",
        )

    if has_inline:
        _ensure(isinstance(receipt["events"], list), "SCHEMA_ERROR", "receipt.events must be array")
        for idx, event in enumerate(receipt["events"]):
            validate_qjcs_object(event, f"receipt.events[{idx}]")

    if has_merkle_fields:
        _validate_sha256_string(receipt["event_root"], "receipt.event_root")
        _ensure(_is_nonneg_int(receipt["event_count"]), "SCHEMA_ERROR", "receipt.event_count invalid")
        _ensure(isinstance(receipt["included_events"], list), "SCHEMA_ERROR", "receipt.included_events must be array")
        for idx, included in enumerate(receipt["included_events"]):
            _check_keys(included, {"event_index", "event_qjcs", "event_proof"}, set(), f"receipt.included_events[{idx}]")
            _ensure(_is_nonneg_int(included["event_index"]), "SCHEMA_ERROR", f"receipt.included_events[{idx}].event_index invalid")
            validate_qjcs_object(included["event_qjcs"], f"receipt.included_events[{idx}].event_qjcs")
            _validate_merkle_proof_structure(included["event_proof"], f"receipt.included_events[{idx}].event_proof")

    if "included_chunks" in receipt:
        _ensure(isinstance(receipt["included_chunks"], list), "SCHEMA_ERROR", "receipt.included_chunks must be array")
        for idx, chunk in enumerate(receipt["included_chunks"]):
            _check_keys(chunk, {"blob_root", "blob_n_chunks", "chunk_index", "chunk_bytes_b64", "chunk_proof"}, set(), f"receipt.included_chunks[{idx}]")
            _validate_sha256_string(chunk["blob_root"], f"receipt.included_chunks[{idx}].blob_root")
            _ensure(_is_pos_int(chunk["blob_n_chunks"]), "SCHEMA_ERROR", "blob_n_chunks invalid")
            _ensure(_is_nonneg_int(chunk["chunk_index"]), "SCHEMA_ERROR", "chunk_index invalid")
            _validate_b64_canonical(chunk["chunk_bytes_b64"], f"receipt.included_chunks[{idx}].chunk_bytes_b64")
            _validate_merkle_proof_structure(chunk["chunk_proof"], f"receipt.included_chunks[{idx}].chunk_proof")

    if "assertions" in receipt:
        _ensure(isinstance(receipt["assertions"], list), "SCHEMA_ERROR", "receipt.assertions must be array")
        for idx, assertion in enumerate(receipt["assertions"]):
            _check_keys(assertion, {"at", "predicate"}, set(), f"receipt.assertions[{idx}]")
            _check_keys(assertion["at"], {"event"}, set(), f"receipt.assertions[{idx}].at")
            _ensure(_is_nonneg_int(assertion["at"]["event"]), "SCHEMA_ERROR", "assertion at.event invalid")
            _check_keys(assertion["predicate"], {"opvm"}, set(), f"receipt.assertions[{idx}].predicate")

    if "extensions" in receipt:
        _ensure(isinstance(receipt["extensions"], dict), "SCHEMA_ERROR", "receipt.extensions must be object")


def validate_glue_receipt(receipt: Any) -> None:
    required = {
        "pcs_version",
        "receipt_type",
        "from_contract_id",
        "to_contract_id",
        "from_invocation_id",
        "to_invocation_id",
        "mapping",
        "bridge_assertions",
        "bounds",
    }
    optional = {"extensions"}
    _check_keys(receipt, required, optional, "glue_receipt")

    _ensure(receipt["pcs_version"] == "v1", "VERSION_UNSUPPORTED", "glue_receipt.pcs_version must be v1")
    _ensure(receipt["receipt_type"] == "GLUE", "SCHEMA_ERROR", "glue_receipt.receipt_type must be GLUE")
    _validate_sha256_string(receipt["from_contract_id"], "glue_receipt.from_contract_id")
    _validate_sha256_string(receipt["to_contract_id"], "glue_receipt.to_contract_id")
    _validate_sha256_string(receipt["from_invocation_id"], "glue_receipt.from_invocation_id")
    _validate_sha256_string(receipt["to_invocation_id"], "glue_receipt.to_invocation_id")

    mapping = receipt["mapping"]
    _ensure(isinstance(mapping, list) and len(mapping) >= 1, "SCHEMA_ERROR", "glue mapping must be non-empty")
    _ensure(len(mapping) <= DEPLOYMENT_PROFILE["max_glue_mappings"], "BOUND_VIOLATION", "too many glue mappings")
    seen_to: set[str] = set()
    for idx, item in enumerate(mapping):
        _check_keys(item, {"from", "to"}, set(), f"glue_receipt.mapping[{idx}]")
        _validate_restricted_pointer(item["from"], f"glue_receipt.mapping[{idx}].from")
        _validate_to_in_pointer(item["to"], f"glue_receipt.mapping[{idx}].to")
        _ensure(item["to"] not in seen_to, "SCHEMA_ERROR", "duplicate to path in glue mapping")
        seen_to.add(item["to"])

    bridge_assertions = receipt["bridge_assertions"]
    _ensure(isinstance(bridge_assertions, list), "SCHEMA_ERROR", "bridge_assertions must be array")
    _ensure(len(bridge_assertions) <= DEPLOYMENT_PROFILE["max_predicates"], "BOUND_VIOLATION", "too many bridge assertions")
    for idx, assertion in enumerate(bridge_assertions):
        _check_keys(assertion, {"predicate"}, set(), f"glue_receipt.bridge_assertions[{idx}]")
        _check_keys(assertion["predicate"], {"opvm"}, set(), f"glue_receipt.bridge_assertions[{idx}].predicate")

    bounds = receipt["bounds"]
    _check_keys(bounds, {"max_receipt_bytes", "gas_predicate"}, set(), "glue_receipt.bounds")
    _ensure(_is_pos_int(bounds["max_receipt_bytes"]), "SCHEMA_ERROR", "glue bounds.max_receipt_bytes invalid")
    _ensure(_is_pos_int(bounds["gas_predicate"]), "SCHEMA_ERROR", "glue bounds.gas_predicate invalid")

    if "extensions" in receipt:
        _ensure(isinstance(receipt["extensions"], dict), "SCHEMA_ERROR", "glue_receipt.extensions must be object")


def _validate_invocation_header_against_profile(invocation_header: dict[str, Any]) -> None:
    header_bytes = len(qjcs_canonical_bytes(invocation_header))
    _ensure(header_bytes <= DEPLOYMENT_PROFILE["max_invocation_header_bytes"], "BOUND_VIOLATION", "invocation header bytes exceed deployment profile")


def _validate_vtr_receipt_against_profile(receipt: dict[str, Any]) -> None:
    receipt_bytes = len(qjcs_canonical_bytes(receipt))
    _ensure(receipt_bytes <= DEPLOYMENT_PROFILE["max_receipt_bytes"], "BOUND_VIOLATION", "receipt bytes exceed deployment profile")


def _validate_glue_receipt_against_profile(receipt: dict[str, Any]) -> None:
    receipt_bytes = len(qjcs_canonical_bytes(receipt))
    _ensure(receipt_bytes <= DEPLOYMENT_PROFILE["max_glue_receipt_bytes"], "BOUND_VIOLATION", "glue receipt bytes exceed deployment profile")
    _ensure(receipt_bytes <= receipt["bounds"]["max_receipt_bytes"], "BOUND_VIOLATION", "glue receipt bytes exceed glue bounds.max_receipt_bytes")
    _ensure(receipt["bounds"]["max_receipt_bytes"] <= DEPLOYMENT_PROFILE["max_glue_receipt_bytes"], "BOUND_VIOLATION", "glue bounds.max_receipt_bytes exceeds deployment profile")
    _ensure(receipt["bounds"]["gas_predicate"] <= DEPLOYMENT_PROFILE["max_gas_predicate"], "BOUND_VIOLATION", "glue bounds.gas_predicate exceeds deployment profile")


def _pointer_get(root: Any, pointer: str) -> Any:
    if pointer == "":
        return root
    _ensure(isinstance(pointer, str), "SCHEMA_ERROR", "pointer must be string")
    _ensure(pointer.startswith("/"), "SCHEMA_ERROR", "pointer must start with '/'")
    current = root
    for raw_segment in pointer.split("/")[1:]:
        _ensure(raw_segment != "", "SCHEMA_ERROR", "pointer segment must not be empty")
        _ensure(POINTER_SEGMENT_RE.match(raw_segment) is not None, "SCHEMA_ERROR", f"invalid pointer segment: {raw_segment}")
        if isinstance(current, dict):
            if raw_segment not in current:
                return None
            current = current[raw_segment]
            continue
        if isinstance(current, list):
            if not raw_segment.isdigit():
                return None
            idx = int(raw_segment)
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
            continue
        return None
    return current


def _u64le(value: int, context: str) -> bytes:
    _ensure(_is_int(value), "SCHEMA_ERROR", f"{context} must be integer")
    _ensure(0 <= value <= U64_MAX, "SCHEMA_ERROR", f"{context} out of u64 range")
    return value.to_bytes(8, byteorder="little", signed=False)


def _merkle_leaf_hash(domain_tag: str, leaf_count: int, leaf_index: int, leaf_bytes: bytes) -> bytes:
    tag = domain_tag.encode("ascii")
    payload = MERKLE_LEAF_PREFIX + tag + _u64le(leaf_count, "leaf_count") + _u64le(leaf_index, "leaf_index") + leaf_bytes
    return _sha256_raw(payload)


def _merkle_node_hash(domain_tag: str, left_hash: bytes, right_hash: bytes) -> bytes:
    tag = domain_tag.encode("ascii")
    payload = MERKLE_NODE_PREFIX + tag + left_hash + right_hash
    return _sha256_raw(payload)


def _verify_merkle_inclusion(*, domain_tag: str, root_sha256: str, leaf_count: int, leaf_index: int, leaf_bytes: bytes, proof: list[dict[str, Any]], max_proof_len: int, context: str) -> None:
    _ensure(len(proof) <= max_proof_len, "BOUND_VIOLATION", f"{context}: proof length exceeds max_proof_len")
    _ensure(leaf_count > 0, "PROOF_INVALID", f"{context}: leaf_count must be positive")
    _ensure(0 <= leaf_index < leaf_count, "PROOF_INVALID", f"{context}: leaf_index out of range")

    acc = _merkle_leaf_hash(domain_tag, leaf_count, leaf_index, leaf_bytes)
    for idx, item in enumerate(proof):
        _check_keys(item, {"dir", "hash"}, set(), f"{context}.proof[{idx}]")
        direction = item["dir"]
        _ensure(direction in {"L", "R"}, "SCHEMA_ERROR", f"{context}.proof[{idx}].dir invalid")
        sibling = _sha256_bytes_from_prefixed(item["hash"], f"{context}.proof[{idx}].hash")
        if direction == "L":
            acc = _merkle_node_hash(domain_tag, sibling, acc)
        else:
            acc = _merkle_node_hash(domain_tag, acc, sibling)

    root_bytes = _sha256_bytes_from_prefixed(root_sha256, f"{context}.root")
    _ensure(acc == root_bytes, "PROOF_INVALID", f"{context}: merkle proof mismatch")

@dataclass
class EvalBudget:
    gas_limit: int
    gas_per_byte: int
    node_limit: int
    gas_used: int = 0
    nodes_used: int = 0

    def charge(self) -> None:
        self.gas_used += 1
        self.nodes_used += 1
        if self.gas_used > self.gas_limit:
            raise VerifyError("GAS_EXHAUSTED", "predicate gas exhausted")
        if self.nodes_used > self.node_limit:
            raise VerifyError("BOUND_VIOLATION", "max_ast_nodes exceeded")

    def charge_value_bytes(self, value: Any, max_value_bytes: int) -> None:
        if value is None:
            return
        qbytes = len(qjcs_canonical_bytes(value))
        if qbytes > max_value_bytes:
            raise VerifyError("BOUND_VIOLATION", f"reachable value exceeds max_value_bytes ({qbytes}>{max_value_bytes})")
        self.gas_used += qbytes * self.gas_per_byte
        if self.gas_used > self.gas_limit:
            raise VerifyError("GAS_EXHAUSTED", "predicate gas exhausted")


def _checked_binary_args(op: str, arg: Any) -> tuple[Any, Any]:
    _ensure(isinstance(arg, list) and len(arg) == 2, "SCHEMA_ERROR", f"{op} expects 2 args")
    return arg[0], arg[1]


def _checked_nary_args(op: str, arg: Any, min_len: int = 1) -> list[Any]:
    _ensure(isinstance(arg, list) and len(arg) >= min_len, "SCHEMA_ERROR", f"{op} expects list")
    return arg


def _i64_arith(value: int, overflow_mode: str) -> int:
    if I64_MIN <= value <= I64_MAX:
        return value
    if overflow_mode == "saturate":
        return I64_MIN if value < I64_MIN else I64_MAX
    raise VerifyError("TYPE_ERROR", "i64 overflow")


def eval_expr(expr: Any, env: dict[str, Any], budget: EvalBudget, overflow_mode: str, max_value_bytes: int) -> Any:
    budget.charge()
    _ensure(isinstance(expr, dict), "SCHEMA_ERROR", "expression must be object")
    _ensure(len(expr) == 1, "SCHEMA_ERROR", "expression must have one operator key")
    op, arg = next(iter(expr.items()))

    if op == "const":
        validate_qjcs_value(arg)
        budget.charge_value_bytes(arg, max_value_bytes)
        return arg

    if op == "get":
        _ensure(isinstance(arg, str), "SCHEMA_ERROR", "get expects string pointer")
        _validate_get_pointer_syntax(arg)
        value = _pointer_get(env, arg)
        budget.charge_value_bytes(value, max_value_bytes)
        return value

    if op in {"eq", "ne"}:
        a_raw, b_raw = _checked_binary_args(op, arg)
        a = eval_expr(a_raw, env, budget, overflow_mode, max_value_bytes)
        b = eval_expr(b_raw, env, budget, overflow_mode, max_value_bytes)
        return (a == b) if op == "eq" else (a != b)

    if op in {"gt", "ge", "lt", "le"}:
        a_raw, b_raw = _checked_binary_args(op, arg)
        a = eval_expr(a_raw, env, budget, overflow_mode, max_value_bytes)
        b = eval_expr(b_raw, env, budget, overflow_mode, max_value_bytes)
        if a is None or b is None:
            return None
        _ensure(_is_int(a) and _is_int(b), "TYPE_ERROR", f"{op} requires integer args")
        if op == "gt":
            return a > b
        if op == "ge":
            return a >= b
        if op == "lt":
            return a < b
        return a <= b

    if op in {"add", "sub", "mul"}:
        a_raw, b_raw = _checked_binary_args(op, arg)
        a = eval_expr(a_raw, env, budget, overflow_mode, max_value_bytes)
        b = eval_expr(b_raw, env, budget, overflow_mode, max_value_bytes)
        if a is None or b is None:
            return None
        _ensure(_is_int(a) and _is_int(b), "TYPE_ERROR", f"{op} requires integer args")
        if op == "add":
            return _i64_arith(a + b, overflow_mode)
        if op == "sub":
            return _i64_arith(a - b, overflow_mode)
        return _i64_arith(a * b, overflow_mode)

    if op == "not":
        value = eval_expr(arg, env, budget, overflow_mode, max_value_bytes)
        if value is None:
            return None
        _ensure(isinstance(value, bool), "TYPE_ERROR", "not requires boolean")
        return not value

    if op in {"and", "or"}:
        args = _checked_nary_args(op, arg, min_len=1)
        saw_none = False
        for item in args:
            value = eval_expr(item, env, budget, overflow_mode, max_value_bytes)
            if value is None:
                saw_none = True
                continue
            _ensure(isinstance(value, bool), "TYPE_ERROR", f"{op} requires boolean args")
            if op == "and" and value is False:
                return False
            if op == "or" and value is True:
                return True
        if saw_none:
            return None
        return True if op == "and" else False

    if op == "in":
        needle_raw, haystack_raw = _checked_binary_args(op, arg)
        needle = eval_expr(needle_raw, env, budget, overflow_mode, max_value_bytes)
        haystack = eval_expr(haystack_raw, env, budget, overflow_mode, max_value_bytes)
        if haystack is None:
            return None
        _ensure(isinstance(haystack, list), "TYPE_ERROR", "in expects list as second arg")
        return any(item == needle for item in haystack)

    raise VerifyError("SCHEMA_ERROR", f"unsupported operator: {op}")


def _predicate_true_or_fail(
    predicate_object: dict[str, Any],
    env: dict[str, Any],
    gas_limit: int,
    gas_per_byte: int,
    node_limit: int,
    max_value_bytes: int,
    overflow_mode: str,
    fail_code: str,
) -> int:
    budget = EvalBudget(gas_limit=gas_limit, gas_per_byte=gas_per_byte, node_limit=node_limit)
    value = eval_expr(predicate_object["opvm"], env, budget, overflow_mode, max_value_bytes)
    if value is True:
        return budget.gas_used
    if value is False or value is None:
        raise VerifyError(fail_code, f"{fail_code} predicate was not true")
    raise VerifyError("TYPE_ERROR", "predicate must evaluate to boolean")

@dataclass
class ProcessedEvidence:
    trace_mode: str
    trace_env: dict[str, Any]
    event_map: dict[int, Any]
    included_events_env: list[dict[str, Any]]
    included_chunks_env: list[dict[str, Any]]
    inclusion_proofs: int
    proven_bytes: int
    chunks_declared: bool


def _process_vtr_evidence(receipt: dict[str, Any], contract: dict[str, Any], max_depth: int, max_value_bytes: int) -> ProcessedEvidence:
    bounds = contract["resource_bounds"]
    max_steps = bounds["max_steps"]
    max_proof_len = bounds["max_proof_len"]
    max_inclusion_proofs = bounds["max_inclusion_proofs"]
    max_proven_bytes = bounds["max_proven_bytes"]
    chunk_bytes = contract["canonicalization"]["tensor_chunk_bytes"]

    event_map: dict[int, Any] = {}
    included_events_env: list[dict[str, Any]] = []
    included_chunks_env: list[dict[str, Any]] = []

    inclusion_proofs = 0
    proven_bytes = 0

    has_inline = "events" in receipt
    has_merkle = all(k in receipt for k in ("event_root", "event_count", "included_events"))

    if has_inline and has_merkle:
        raise VerifyError("SCHEMA_ERROR", "receipt cannot include both inline and merkle trace")

    if has_inline:
        events = receipt["events"]
        _ensure(isinstance(events, list), "SCHEMA_ERROR", "receipt.events must be array")
        _ensure(len(events) <= max_steps, "BOUND_VIOLATION", "inline events exceed max_steps")
        for idx, event in enumerate(events):
            validate_qjcs_object(event, f"receipt.events[{idx}]")
            _enforce_value_bounds(event, max_depth, max_value_bytes, f"/trace/events[{idx}]")
            event_map[idx] = event
        trace_env = {"event_count": len(events), "events": events, "event_root": None}
        trace_mode = "inline"
    elif has_merkle:
        event_root = receipt["event_root"]
        event_count = receipt["event_count"]
        included_events = receipt["included_events"]

        _validate_sha256_string(event_root, "receipt.event_root")
        _ensure(_is_nonneg_int(event_count), "SCHEMA_ERROR", "receipt.event_count invalid")
        _ensure(event_count <= max_steps, "BOUND_VIOLATION", "event_count exceeds max_steps")
        _ensure(isinstance(included_events, list), "SCHEMA_ERROR", "included_events must be array")

        seen_event_indices: set[int] = set()
        for idx, item in enumerate(included_events):
            event_index = item["event_index"]
            event_obj = item["event_qjcs"]
            proof = item["event_proof"]

            _ensure(event_index < event_count, "PROOF_INVALID", "included event_index out of range")
            _ensure(event_index not in seen_event_indices, "PROOF_INVALID", "duplicate event_index in included_events")
            seen_event_indices.add(event_index)

            validate_qjcs_object(event_obj, f"receipt.included_events[{idx}].event_qjcs")
            _enforce_value_bounds(event_obj, max_depth, max_value_bytes, f"/receipt/included_events[{idx}]")

            _validate_merkle_proof_structure(proof, f"receipt.included_events[{idx}].event_proof")
            _verify_merkle_inclusion(
                domain_tag=MERKLE_DOMAIN_EVENTLOG,
                root_sha256=event_root,
                leaf_count=event_count,
                leaf_index=event_index,
                leaf_bytes=qjcs_canonical_bytes(event_obj),
                proof=proof,
                max_proof_len=max_proof_len,
                context=f"included_events[{idx}]",
            )

            event_map[event_index] = event_obj
            included_events_env.append({"event_index": event_index, "event_qjcs": event_obj})
            inclusion_proofs += 1
            proven_bytes += len(qjcs_canonical_bytes(event_obj))

        trace_env = {"event_count": event_count, "events": None, "event_root": event_root}
        trace_mode = "merkle"
    else:
        trace_env = {"event_count": 0, "events": None, "event_root": None}
        trace_mode = "none"

    chunks_declared = "included_chunks" in receipt
    included_chunks = receipt.get("included_chunks", [])
    _ensure(isinstance(included_chunks, list), "SCHEMA_ERROR", "included_chunks must be array")
    seen_chunks: set[tuple[str, int]] = set()

    for idx, item in enumerate(included_chunks):
        blob_root = item["blob_root"]
        blob_n_chunks = item["blob_n_chunks"]
        chunk_index = item["chunk_index"]
        chunk_b64 = item["chunk_bytes_b64"]
        chunk_proof = item["chunk_proof"]

        _validate_sha256_string(blob_root, f"receipt.included_chunks[{idx}].blob_root")
        _ensure(_is_pos_int(blob_n_chunks), "SCHEMA_ERROR", "blob_n_chunks invalid")
        _ensure(_is_nonneg_int(chunk_index), "SCHEMA_ERROR", "chunk_index invalid")
        _ensure(chunk_index < blob_n_chunks, "PROOF_INVALID", "chunk_index out of range")

        _validate_b64_canonical(chunk_b64, f"receipt.included_chunks[{idx}].chunk_bytes_b64")
        raw_chunk = base64.b64decode(chunk_b64, validate=True)
        chunk_len = len(raw_chunk)

        _ensure(chunk_len > 0, "PROOF_INVALID", "chunk bytes must be non-empty")
        _ensure(chunk_len <= chunk_bytes, "PROOF_INVALID", "chunk bytes exceed tensor_chunk_bytes")
        _ensure(chunk_len <= bounds["max_chunk_bytes"], "BOUND_VIOLATION", "chunk bytes exceed max_chunk_bytes")

        if chunk_index < blob_n_chunks - 1:
            _ensure(chunk_len == chunk_bytes, "PROOF_INVALID", "non-final chunks must have full chunk_bytes")

        chunk_key = (blob_root, chunk_index)
        _ensure(chunk_key not in seen_chunks, "PROOF_INVALID", "duplicate (blob_root,chunk_index) in included_chunks")
        seen_chunks.add(chunk_key)

        _validate_merkle_proof_structure(chunk_proof, f"receipt.included_chunks[{idx}].chunk_proof")
        _verify_merkle_inclusion(
            domain_tag=MERKLE_DOMAIN_BLOB,
            root_sha256=blob_root,
            leaf_count=blob_n_chunks,
            leaf_index=chunk_index,
            leaf_bytes=raw_chunk,
            proof=chunk_proof,
            max_proof_len=max_proof_len,
            context=f"included_chunks[{idx}]",
        )

        included_chunks_env.append(
            {
                "blob_root": blob_root,
                "blob_n_chunks": blob_n_chunks,
                "chunk_index": chunk_index,
                "chunk_bytes_b64": chunk_b64,
            }
        )
        inclusion_proofs += 1
        proven_bytes += chunk_len

    _ensure(inclusion_proofs <= max_inclusion_proofs, "BOUND_VIOLATION", "max_inclusion_proofs exceeded")
    _ensure(proven_bytes <= max_proven_bytes, "BOUND_VIOLATION", "max_proven_bytes exceeded")

    return ProcessedEvidence(
        trace_mode=trace_mode,
        trace_env=trace_env,
        event_map=event_map,
        included_events_env=included_events_env,
        included_chunks_env=included_chunks_env,
        inclusion_proofs=inclusion_proofs,
        proven_bytes=proven_bytes,
        chunks_declared=chunks_declared,
    )


@dataclass
class VerifiedInvocation:
    contract_id: str
    skill_id: str
    invocation_id: str
    gas_used: int
    final_env: dict[str, Any]

def _verify_vtr_bundle_obj(bundle: Any) -> VerifiedInvocation:
    _check_keys(bundle, {"contract", "invocation_header", "in", "out", "receipt"}, {"obs", "out_bytes_b64"}, "bundle")

    contract = bundle["contract"]
    invocation_header = bundle["invocation_header"]
    in_value = bundle["in"]
    out_value = bundle["out"]
    receipt = bundle["receipt"]

    validate_contract(contract)
    validate_invocation_header(invocation_header)
    validate_vtr_receipt(receipt)

    _validate_contract_against_profile(contract)
    _validate_invocation_header_against_profile(invocation_header)
    _validate_vtr_receipt_against_profile(receipt)

    _validate_b64_suffix_fields(invocation_header, "/inv")
    _validate_b64_suffix_fields(receipt, "/receipt")
    if "out_bytes_b64" in bundle:
        _validate_b64_canonical(bundle["out_bytes_b64"], "/out_bytes_b64")

    max_depth = contract["predicate_vm"]["max_depth"]
    max_value_bytes = contract["predicate_vm"]["max_value_bytes"]

    recomputed_contract_id = recompute_contract_id(contract)
    _ensure(contract["contract_id"] == recomputed_contract_id, "CONTRACT_INVALID", "contract_id mismatch")

    _ensure(invocation_header["contract_id"] == contract["contract_id"], "INVOCATION_MISMATCH", "invocation header contract_id mismatch")
    _ensure(receipt["contract_id"] == contract["contract_id"], "INVOCATION_MISMATCH", "receipt contract_id mismatch")
    _ensure(receipt["skill_id"] == contract["skill_id"], "INVOCATION_MISMATCH", "receipt skill_id mismatch")

    computed_input_hash = qjcs_hash(in_value)
    _ensure(invocation_header["input_json_qjcs_sha256"] == computed_input_hash, "INVOCATION_MISMATCH", "input commitment mismatch")

    invocation_id = recompute_invocation_id(invocation_header)
    _ensure(receipt["invocation_id"] == invocation_id, "INVOCATION_MISMATCH", "invocation_id mismatch")

    input_bytes = len(qjcs_canonical_bytes(in_value))
    _ensure(input_bytes <= contract["io"]["input"]["max_bytes"], "BOUND_VIOLATION", "input exceeds io.input.max_bytes")
    _enforce_value_bounds(in_value, max_depth, max_value_bytes, "/in")

    output_mode = contract["io"]["output"]["mode"]
    output_commitment: dict[str, str]
    post_out_value: Any

    if output_mode == "json":
        _ensure("out_bytes_b64" not in bundle, "SCHEMA_ERROR", "json output mode forbids out_bytes_b64")
        output_bytes = len(qjcs_canonical_bytes(out_value))
        _ensure(output_bytes <= contract["io"]["output"]["max_bytes"], "BOUND_VIOLATION", "output exceeds io.output.max_bytes")
        _enforce_value_bounds(out_value, max_depth, max_value_bytes, "/out")

        computed_out_hash = qjcs_hash(out_value)
        _ensure("out_json_qjcs_sha256" in receipt, "SCHEMA_ERROR", "json output requires out_json_qjcs_sha256")
        _ensure(receipt["out_json_qjcs_sha256"] == computed_out_hash, "OUTPUT_MISMATCH", "output hash mismatch")
        output_commitment = {"out_json_qjcs_sha256": computed_out_hash}
        post_out_value = out_value
    else:
        _ensure(out_value is None, "SCHEMA_ERROR", "bytes output mode expects out to be null")
        _ensure("out_bytes_b64" in bundle, "SCHEMA_ERROR", "bytes output mode requires out_bytes_b64")
        raw_bytes = base64.b64decode(bundle["out_bytes_b64"], validate=True)
        _ensure(len(raw_bytes) <= contract["io"]["output"]["max_bytes"], "BOUND_VIOLATION", "output exceeds io.output.max_bytes")
        _ensure("out_bytes_sha256" in receipt, "SCHEMA_ERROR", "bytes output requires out_bytes_sha256")
        computed_out_hash = raw_bytes_hash(raw_bytes)
        _ensure(receipt["out_bytes_sha256"] == computed_out_hash, "OUTPUT_MISMATCH", "output hash mismatch")
        output_commitment = {"out_bytes_sha256": computed_out_hash}
        post_out_value = None

    receipt_bytes = qjcs_canonical_bytes(receipt)
    _ensure(len(receipt_bytes) <= contract["resource_bounds"]["max_receipt_bytes"], "BOUND_VIOLATION", "max_receipt_bytes exceeded")

    evidence = _process_vtr_evidence(receipt, contract, max_depth, max_value_bytes)

    _ensure("VTR" in contract["receipt_rule"]["allowed_profiles"], "CONTRACT_INVALID", "VTR profile is not allowed")
    max_assertions = contract["receipt_rule"]["profile_params"]["VTR"]["max_assertions"]
    assertions = receipt.get("assertions", [])
    _ensure(len(assertions) <= max_assertions, "BOUND_VIOLATION", "max_assertions exceeded")

    for assertion in assertions:
        _validate_opvm_ast(assertion["predicate"]["opvm"], contract["predicate_vm"]["max_ast_nodes"])

    total_predicates = 2 + len(assertions)
    _ensure(total_predicates <= contract["resource_bounds"]["max_predicates"], "BOUND_VIOLATION", "max_predicates exceeded")

    receipt_env: dict[str, Any] = dict(output_commitment)
    if evidence.trace_mode == "merkle":
        receipt_env["included_events"] = evidence.included_events_env
    if evidence.chunks_declared:
        receipt_env["included_chunks"] = evidence.included_chunks_env

    base_env = {
        "inv": invocation_header,
        "in": in_value,
        "out": None,
        "obs": bundle.get("obs"),
        "trace": evidence.trace_env,
        "receipt": receipt_env,
        "event": None,
        "vars": {},
    }

    gas_limit = contract["predicate_vm"]["gas_predicate"]
    gas_per_byte = contract["predicate_vm"]["gas_per_byte"]
    node_limit = contract["predicate_vm"]["max_ast_nodes"]
    overflow_mode = contract["predicate_vm"]["int_overflow"]

    gas_used = 0

    pre_env = copy.deepcopy(base_env)
    pre_env["out"] = None
    pre_env["event"] = None
    gas_used += _predicate_true_or_fail(contract["precondition"], pre_env, gas_limit, gas_per_byte, node_limit, max_value_bytes, overflow_mode, "PRECOND_FAIL")

    post_env = copy.deepcopy(base_env)
    post_env["out"] = post_out_value
    post_env["event"] = None
    gas_used += _predicate_true_or_fail(contract["postcondition"], post_env, gas_limit, gas_per_byte, node_limit, max_value_bytes, overflow_mode, "POSTCOND_FAIL")

    for idx, assertion in enumerate(assertions):
        at_event = assertion["at"]["event"]
        _ensure(at_event in evidence.event_map, "PROOF_INVALID", f"assertion[{idx}] references unproven event")
        assert_env = copy.deepcopy(base_env)
        assert_env["out"] = post_out_value
        assert_env["event"] = evidence.event_map[at_event]
        gas_used += _predicate_true_or_fail(assertion["predicate"], assert_env, gas_limit, gas_per_byte, node_limit, max_value_bytes, overflow_mode, "ASSERT_FAIL")

    final_env = {
        "inv": invocation_header,
        "in": in_value,
        "out": post_out_value,
        "obs": bundle.get("obs"),
        "trace": evidence.trace_env,
        "receipt": receipt_env,
        "event": None,
        "vars": {},
    }

    return VerifiedInvocation(
        contract_id=contract["contract_id"],
        skill_id=contract["skill_id"],
        invocation_id=invocation_id,
        gas_used=gas_used,
        final_env=final_env,
    )

def _verify_glue_against_transition(glue_receipt: dict[str, Any], from_verified: VerifiedInvocation, to_bundle: dict[str, Any]) -> int:
    _check_keys(to_bundle, {"contract", "invocation_header", "in", "out", "receipt"}, {"obs", "out_bytes_b64"}, "to_bundle")

    to_header = to_bundle["invocation_header"]
    to_in = to_bundle["in"]

    validate_invocation_header(to_header)
    _validate_invocation_header_against_profile(to_header)
    _validate_b64_suffix_fields(to_header, "/to/inv")

    _ensure(glue_receipt["from_contract_id"] == from_verified.contract_id, "INVOCATION_MISMATCH", "glue from_contract_id mismatch")
    _ensure(glue_receipt["from_invocation_id"] == from_verified.invocation_id, "INVOCATION_MISMATCH", "glue from_invocation_id mismatch")

    _ensure(glue_receipt["to_contract_id"] == to_header["contract_id"], "INVOCATION_MISMATCH", "glue to_contract_id mismatch")
    to_invocation_id = recompute_invocation_id(to_header)
    _ensure(glue_receipt["to_invocation_id"] == to_invocation_id, "INVOCATION_MISMATCH", "glue to_invocation_id mismatch")

    to_input_hash = qjcs_hash(to_in)
    _ensure(to_header["input_json_qjcs_sha256"] == to_input_hash, "INVOCATION_MISMATCH", "to_bundle input commitment mismatch")

    from_env = from_verified.final_env
    to_input_env = {"in": to_in}

    seen_to_paths: set[str] = set()
    for idx, item in enumerate(glue_receipt["mapping"]):
        from_path = item["from"]
        to_path = item["to"]
        _ensure(to_path not in seen_to_paths, "SCHEMA_ERROR", "duplicate to path in glue mapping")
        seen_to_paths.add(to_path)

        from_value = _pointer_get(from_env, from_path)
        to_value = _pointer_get(to_input_env, to_path)
        _ensure(from_value == to_value, "INVOCATION_MISMATCH", f"glue mapping mismatch at mapping[{idx}]")

    gas_used = 0
    max_value_bytes = DEPLOYMENT_PROFILE["max_value_bytes"]
    node_limit = DEPLOYMENT_PROFILE["max_ast_nodes"]
    gas_limit = glue_receipt["bounds"]["gas_predicate"]

    glue_env = copy.deepcopy(from_env)
    glue_env["event"] = None
    glue_env["vars"] = {}

    for idx, item in enumerate(glue_receipt["bridge_assertions"]):
        _validate_opvm_ast(item["predicate"]["opvm"], node_limit)
        gas_used += _predicate_true_or_fail(item["predicate"], glue_env, gas_limit, 1, node_limit, max_value_bytes, "reject", "ASSERT_FAIL")

    return gas_used


def verify_bundle_obj(bundle: Any) -> dict[str, Any]:
    verified = _verify_vtr_bundle_obj(bundle)
    return _attach_decision_hash(
        {
            "decision": "ACCEPT",
            "rejection_code": None,
            "contract_id": verified.contract_id,
            "invocation_id": verified.invocation_id,
            "gas_used": verified.gas_used,
            "checker_version": CHECKER_VERSION,
        }
    )


def verify_composition_obj(bundle: Any) -> dict[str, Any]:
    _check_keys(bundle, {"from_bundle", "to_bundle", "glue_receipt"}, set(), "composition_bundle")

    glue_receipt = bundle["glue_receipt"]
    validate_glue_receipt(glue_receipt)
    _validate_glue_receipt_against_profile(glue_receipt)

    from_verified = _verify_vtr_bundle_obj(bundle["from_bundle"])

    glue_gas = _verify_glue_against_transition(
        glue_receipt=glue_receipt,
        from_verified=from_verified,
        to_bundle=bundle["to_bundle"],
    )

    to_verified = _verify_vtr_bundle_obj(bundle["to_bundle"])

    _ensure(glue_receipt["to_contract_id"] == to_verified.contract_id, "INVOCATION_MISMATCH", "glue to_contract_id does not match verified to_bundle")
    _ensure(glue_receipt["to_invocation_id"] == to_verified.invocation_id, "INVOCATION_MISMATCH", "glue to_invocation_id does not match verified to_bundle")

    return _attach_decision_hash(
        {
            "decision": "ACCEPT",
            "rejection_code": None,
            "contract_id": to_verified.contract_id,
            "invocation_id": to_verified.invocation_id,
            "gas_used": from_verified.gas_used + glue_gas + to_verified.gas_used,
            "checker_version": CHECKER_VERSION,
            "from_contract_id": from_verified.contract_id,
            "from_invocation_id": from_verified.invocation_id,
        }
    )


def _is_composition_bundle(candidate: Any) -> bool:
    if not isinstance(candidate, dict):
        return False
    return {"from_bundle", "to_bundle", "glue_receipt"}.issubset(set(candidate.keys()))


def verify_bundle_bytes(raw: bytes) -> dict[str, Any]:
    try:
        if len(raw) > MAX_BUNDLE_BYTES:
            raise VerifyError("BOUND_VIOLATION", "bundle bytes exceed checker input cap")
        bundle = parse_strict_json_bytes(raw)
        if _compute_depth(bundle) > PARSE_MAX_DEPTH:
            raise VerifyError("PARSE_ERROR", "bundle exceeds parse max depth")

        if _is_composition_bundle(bundle):
            return verify_composition_obj(bundle)
        return verify_bundle_obj(bundle)
    except VerifyError as exc:
        return _attach_decision_hash(
            {
                "decision": "REJECT",
                "rejection_code": exc.code,
                "contract_id": None,
                "invocation_id": None,
                "gas_used": 0,
                "checker_version": CHECKER_VERSION,
                "error": exc.message,
            }
        )
