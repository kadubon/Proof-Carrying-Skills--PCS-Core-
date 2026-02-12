
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "test-vectors"
CHECKER = ROOT / "reference-checker" / "pcs_core.py"


def load_pcs_core():
    spec = importlib.util.spec_from_file_location("pcs_core", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load reference checker module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mutate_sha256(value: str) -> str:
    if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
        raise ValueError(f"not a sha256 string: {value}")
    tail = value[-1]
    replacement = "0" if tail != "0" else "1"
    return value[:-1] + replacement


def _u64le(value: int) -> bytes:
    if value < 0 or value > 2**64 - 1:
        raise ValueError("u64 out of range")
    return value.to_bytes(8, byteorder="little", signed=False)


def _sha256_raw(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def _leaf_hash(domain_tag: str, leaf_count: int, leaf_index: int, leaf_bytes: bytes) -> bytes:
    return _sha256_raw(b"PCS-LEAF" + domain_tag.encode("ascii") + _u64le(leaf_count) + _u64le(leaf_index) + leaf_bytes)


def _node_hash(domain_tag: str, left: bytes, right: bytes) -> bytes:
    return _sha256_raw(b"PCS-NODE" + domain_tag.encode("ascii") + left + right)


def merkle_root_and_proofs(domain_tag: str, leaves: list[bytes]) -> tuple[str, list[list[dict[str, str]]]]:
    n = len(leaves)
    if n <= 0:
        raise ValueError("leaves must be non-empty")

    levels: list[list[bytes]] = [[_leaf_hash(domain_tag, n, idx, leaf) for idx, leaf in enumerate(leaves)]]
    while len(levels[-1]) > 1:
        prev = levels[-1]
        nxt: list[bytes] = []
        for i in range(0, len(prev), 2):
            left = prev[i]
            right = prev[i + 1] if i + 1 < len(prev) else prev[i]
            nxt.append(_node_hash(domain_tag, left, right))
        levels.append(nxt)

    proofs: list[list[dict[str, str]]] = []
    for leaf_index in range(n):
        idx = leaf_index
        proof_items: list[dict[str, str]] = []
        for level in levels[:-1]:
            sibling_index = idx ^ 1
            if sibling_index >= len(level):
                sibling_index = idx
            sibling_hash = level[sibling_index]
            direction = "L" if sibling_index < idx else "R"
            proof_items.append({"dir": direction, "hash": "sha256:" + sibling_hash.hex()})
            idx //= 2
        proofs.append(proof_items)

    root = levels[-1][0]
    return "sha256:" + root.hex(), proofs


def build_contract(pcs_core, output_mode: str = "json", tensor_chunk_bytes: int = 4):
    if output_mode not in {"json", "bytes"}:
        raise ValueError("output_mode must be json or bytes")

    postcondition = {
        "opvm": {
            "eq": [
                {"get": "/out/result"},
                {"get": "/in/value"},
            ]
        }
    }
    if output_mode == "bytes":
        postcondition = {"opvm": {"const": True}}

    contract = {
        "pcs_version": "v1",
        "contract_id": "sha256:" + ("0" * 64),
        "skill_id": "demo.echo",
        "version": "1.0.0",
        "ttl_seconds": 86400,
        "io": {
            "input": {"mode": "json", "max_bytes": 4096},
            "output": {"mode": output_mode, "max_bytes": 4096},
        },
        "predicate_vm": {
            "name": "opvm-v1",
            "gas_predicate": 5000,
            "gas_per_byte": 1,
            "max_depth": 32,
            "max_ast_nodes": 512,
            "max_value_bytes": 4096,
            "int_overflow": "reject",
        },
        "canonicalization": {
            "name": "qjcs-v1",
            "default_scale10": 6,
            "tensor_chunk_bytes": tensor_chunk_bytes,
            "max_scale10": 18,
        },
        "precondition": {
            "opvm": {
                "and": [
                    {"eq": [{"get": "/in/task"}, {"const": "echo"}]},
                    {"ge": [{"get": "/in/value"}, {"const": 0}]},
                ]
            }
        },
        "postcondition": postcondition,
        "resource_bounds": {
            "max_receipt_bytes": 65536,
            "max_inclusion_proofs": 32,
            "max_proof_len": 32,
            "max_predicates": 16,
            "max_steps": 16,
            "max_chunk_bytes": 4096,
            "max_proven_bytes": 65536,
        },
        "receipt_rule": {
            "allowed_profiles": ["VTR"],
            "profile_params": {
                "VTR": {
                    "max_assertions": 8,
                }
            },
        },
    }
    contract["contract_id"] = pcs_core.recompute_contract_id(contract)
    return contract


def build_invocation_header(pcs_core, contract_id: str, in_value: dict, nonce_b64: str) -> dict:
    return {
        "pcs_version": "v1",
        "contract_id": contract_id,
        "input_json_qjcs_sha256": pcs_core.qjcs_hash(in_value),
        "nonce_b64": nonce_b64,
    }


def build_bundle_json(
    pcs_core,
    contract: dict,
    in_value: dict,
    out_value: dict,
    nonce_b64: str,
    receipt_extras: dict | None = None,
) -> dict:
    invocation_header = build_invocation_header(pcs_core, contract["contract_id"], in_value, nonce_b64)
    invocation_id = pcs_core.recompute_invocation_id(invocation_header)
    receipt = {
        "pcs_version": "v1",
        "receipt_type": "VTR",
        "contract_id": contract["contract_id"],
        "skill_id": contract["skill_id"],
        "invocation_id": invocation_id,
        "out_json_qjcs_sha256": pcs_core.qjcs_hash(out_value),
    }
    if receipt_extras:
        receipt.update(copy.deepcopy(receipt_extras))
    return {
        "contract": contract,
        "invocation_header": invocation_header,
        "in": in_value,
        "out": out_value,
        "receipt": receipt,
    }


def build_bundle_bytes(
    pcs_core,
    contract: dict,
    in_value: dict,
    out_bytes_b64: str,
    nonce_b64: str,
    receipt_extras: dict | None = None,
) -> dict:
    invocation_header = build_invocation_header(pcs_core, contract["contract_id"], in_value, nonce_b64)
    invocation_id = pcs_core.recompute_invocation_id(invocation_header)
    raw = base64.b64decode(out_bytes_b64, validate=True)
    receipt = {
        "pcs_version": "v1",
        "receipt_type": "VTR",
        "contract_id": contract["contract_id"],
        "skill_id": contract["skill_id"],
        "invocation_id": invocation_id,
        "out_bytes_sha256": pcs_core.raw_bytes_hash(raw),
    }
    if receipt_extras:
        receipt.update(copy.deepcopy(receipt_extras))
    return {
        "contract": contract,
        "invocation_header": invocation_header,
        "in": in_value,
        "out": None,
        "out_bytes_b64": out_bytes_b64,
        "receipt": receipt,
    }

def build_inline_assertion(event_index: int, predicate_opvm: dict) -> dict:
    return {
        "at": {"event": event_index},
        "predicate": {"opvm": predicate_opvm},
    }


def build_merkle_trace_extras(pcs_core, events: list[dict], include_indices: list[int]) -> dict:
    qjcs_leaves = [pcs_core.qjcs_canonical_bytes(ev) for ev in events]
    root, proofs = merkle_root_and_proofs("PCS-EVENTLOG", qjcs_leaves)
    included_events: list[dict] = []
    for idx in include_indices:
        included_events.append(
            {
                "event_index": idx,
                "event_qjcs": events[idx],
                "event_proof": proofs[idx],
            }
        )
    return {
        "event_root": root,
        "event_count": len(events),
        "included_events": included_events,
    }


def build_blob_chunk_entries(chunks: list[bytes], include_indices: list[int]) -> list[dict]:
    blob_root, proofs = merkle_root_and_proofs("PCS-BLOB", chunks)
    n_chunks = len(chunks)
    out: list[dict] = []
    for idx in include_indices:
        out.append(
            {
                "blob_root": blob_root,
                "blob_n_chunks": n_chunks,
                "chunk_index": idx,
                "chunk_bytes_b64": base64.b64encode(chunks[idx]).decode("ascii"),
                "chunk_proof": proofs[idx],
            }
        )
    return out


def write_vector(pcs_core, path: Path, bundle: dict | str, decision: str, rejection_code: str | None) -> None:
    if isinstance(bundle, str):
        raw = bundle.encode("utf-8")
        parsed = None
    else:
        raw = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        parsed = bundle

    actual = pcs_core.verify_bundle_bytes(raw)
    if actual["decision"] != decision or actual["rejection_code"] != rejection_code:
        raise RuntimeError(
            f"vector expectation mismatch at {path}: expected=({decision},{rejection_code}) "
            f"actual=({actual['decision']},{actual['rejection_code']})"
        )

    path.mkdir(parents=True, exist_ok=True)
    if parsed is None:
        (path / "bundle.json").write_text(bundle + "\n", encoding="utf-8")
    else:
        write_json(path / "bundle.json", parsed)

    write_json(
        path / "expected.json",
        {
            "decision": decision,
            "rejection_code": rejection_code,
            "decision_sha256": actual["decision_sha256"],
        },
    )


def main() -> int:
    pcs_core = load_pcs_core()
    nonce = "AQEBAQEBAQEBAQEBAQEBAQ=="

    # Rebuild only bundle vectors; keep schema vectors untouched.
    shutil.rmtree(VECTORS / "accept", ignore_errors=True)
    shutil.rmtree(VECTORS / "reject", ignore_errors=True)

    contract_json = build_contract(pcs_core, output_mode="json", tensor_chunk_bytes=4)
    accept_bundle = build_bundle_json(
        pcs_core,
        contract=copy.deepcopy(contract_json),
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
    )
    write_vector(pcs_core, VECTORS / "accept" / "basic_echo", accept_bundle, "ACCEPT", None)

    contract_bytes = build_contract(pcs_core, output_mode="bytes", tensor_chunk_bytes=4)
    accept_bytes_bundle = build_bundle_bytes(
        pcs_core,
        contract=copy.deepcopy(contract_bytes),
        in_value={"task": "echo", "value": 7},
        out_bytes_b64="aGVsbG8=",
        nonce_b64=nonce,
    )
    write_vector(pcs_core, VECTORS / "accept" / "basic_echo_bytes", accept_bytes_bundle, "ACCEPT", None)

    inline_events = [{"tool": "echo", "ok": True}]
    inline_bundle = build_bundle_json(
        pcs_core,
        contract=copy.deepcopy(contract_json),
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
        receipt_extras={
            "events": inline_events,
            "assertions": [
                build_inline_assertion(
                    0,
                    {
                        "eq": [
                            {"get": "/event/tool"},
                            {"const": "echo"},
                        ]
                    },
                )
            ],
        },
    )
    write_vector(pcs_core, VECTORS / "accept" / "vtr_inline_trace_assert", inline_bundle, "ACCEPT", None)

    merkle_events = [
        {"kind": "tool_call", "ok": True},
        {"kind": "tool_result", "ok": True},
    ]
    merkle_extras = build_merkle_trace_extras(pcs_core, merkle_events, include_indices=[1])
    chunks = [b"abcd", b"xy"]
    merkle_extras["included_chunks"] = build_blob_chunk_entries(chunks, include_indices=[0])
    merkle_extras["assertions"] = [
        build_inline_assertion(
            1,
            {
                "eq": [
                    {"get": "/event/ok"},
                    {"const": True},
                ]
            },
        )
    ]
    merkle_bundle = build_bundle_json(
        pcs_core,
        contract=copy.deepcopy(contract_json),
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
        receipt_extras=merkle_extras,
    )
    write_vector(pcs_core, VECTORS / "accept" / "vtr_merkle_blob", merkle_bundle, "ACCEPT", None)

    from_bundle = copy.deepcopy(inline_bundle)
    to_bundle = build_bundle_json(
        pcs_core,
        contract=copy.deepcopy(contract_json),
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64="AgICAgICAgICAgICAgICAg==",
    )
    glue_receipt = {
        "pcs_version": "v1",
        "receipt_type": "GLUE",
        "from_contract_id": from_bundle["contract"]["contract_id"],
        "to_contract_id": to_bundle["contract"]["contract_id"],
        "from_invocation_id": from_bundle["receipt"]["invocation_id"],
        "to_invocation_id": to_bundle["receipt"]["invocation_id"],
        "mapping": [
            {"from": "/out/result", "to": "/in/value"},
            {"from": "/in/task", "to": "/in/task"},
        ],
        "bridge_assertions": [
            {
                "predicate": {
                    "opvm": {
                        "eq": [
                            {"get": "/out/result"},
                            {"const": 7},
                        ]
                    }
                }
            }
        ],
        "bounds": {
            "max_receipt_bytes": 4096,
            "gas_predicate": 5000,
        },
    }
    composition_bundle = {
        "from_bundle": from_bundle,
        "to_bundle": to_bundle,
        "glue_receipt": glue_receipt,
    }
    write_vector(pcs_core, VECTORS / "accept" / "glue_composition", composition_bundle, "ACCEPT", None)

    contract_id_mismatch = copy.deepcopy(accept_bundle)
    bad_contract_id = mutate_sha256(contract_id_mismatch["contract"]["contract_id"])
    contract_id_mismatch["contract"]["contract_id"] = bad_contract_id
    contract_id_mismatch["invocation_header"]["contract_id"] = bad_contract_id
    contract_id_mismatch["receipt"]["contract_id"] = bad_contract_id
    write_vector(pcs_core, VECTORS / "reject" / "contract_id_mismatch", contract_id_mismatch, "REJECT", "CONTRACT_INVALID")

    invocation_mismatch = copy.deepcopy(accept_bundle)
    invocation_mismatch["invocation_header"]["nonce_b64"] = "AwMDAwMDAwMDAwMDAwMDAw=="
    write_vector(pcs_core, VECTORS / "reject" / "invocation_mismatch", invocation_mismatch, "REJECT", "INVOCATION_MISMATCH")

    output_hash_mismatch = copy.deepcopy(accept_bundle)
    output_hash_mismatch["receipt"]["out_json_qjcs_sha256"] = mutate_sha256(output_hash_mismatch["receipt"]["out_json_qjcs_sha256"])
    write_vector(pcs_core, VECTORS / "reject" / "output_hash_mismatch", output_hash_mismatch, "REJECT", "OUTPUT_MISMATCH")

    output_bytes_hash_mismatch = copy.deepcopy(accept_bytes_bundle)
    output_bytes_hash_mismatch["receipt"]["out_bytes_sha256"] = mutate_sha256(output_bytes_hash_mismatch["receipt"]["out_bytes_sha256"])
    write_vector(pcs_core, VECTORS / "reject" / "output_bytes_hash_mismatch", output_bytes_hash_mismatch, "REJECT", "OUTPUT_MISMATCH")

    precondition_fail = build_bundle_json(
        pcs_core,
        contract=copy.deepcopy(contract_json),
        in_value={"task": "echo", "value": -1},
        out_value={"result": -1},
        nonce_b64=nonce,
    )
    write_vector(pcs_core, VECTORS / "reject" / "precondition_fail", precondition_fail, "REJECT", "PRECOND_FAIL")

    base64_invalid = copy.deepcopy(accept_bundle)
    base64_invalid["invocation_header"]["nonce_b64"] = "AQEBAQEBAQEBAQEBAQEBAQ"
    write_vector(pcs_core, VECTORS / "reject" / "base64_invalid_nonce", base64_invalid, "REJECT", "BASE64_INVALID")

    bytes_missing_out_b64 = copy.deepcopy(accept_bytes_bundle)
    bytes_missing_out_b64.pop("out_bytes_b64", None)
    write_vector(pcs_core, VECTORS / "reject" / "bytes_missing_out_b64", bytes_missing_out_b64, "REJECT", "SCHEMA_ERROR")

    gas_exhausted_contract = copy.deepcopy(contract_json)
    gas_exhausted_contract["predicate_vm"]["gas_predicate"] = 2
    gas_exhausted_contract["contract_id"] = pcs_core.recompute_contract_id(gas_exhausted_contract)
    gas_exhausted_bundle = build_bundle_json(
        pcs_core,
        contract=gas_exhausted_contract,
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
    )
    write_vector(pcs_core, VECTORS / "reject" / "gas_exhausted", gas_exhausted_bundle, "REJECT", "GAS_EXHAUSTED")

    value_bound_contract = copy.deepcopy(contract_json)
    value_bound_contract["predicate_vm"]["max_value_bytes"] = 4
    value_bound_contract["contract_id"] = pcs_core.recompute_contract_id(value_bound_contract)
    value_bound_bundle = build_bundle_json(
        pcs_core,
        contract=value_bound_contract,
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
    )
    write_vector(pcs_core, VECTORS / "reject" / "value_bytes_exceeded", value_bound_bundle, "REJECT", "BOUND_VIOLATION")

    profile_depth_violation = copy.deepcopy(contract_json)
    profile_depth_violation["predicate_vm"]["max_depth"] = pcs_core.DEPLOYMENT_PROFILE["max_depth"] + 1
    profile_depth_violation["contract_id"] = pcs_core.recompute_contract_id(profile_depth_violation)
    profile_depth_bundle = build_bundle_json(
        pcs_core,
        contract=profile_depth_violation,
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
    )
    write_vector(pcs_core, VECTORS / "reject" / "profile_depth_exceeds_profile", profile_depth_bundle, "REJECT", "CONTRACT_INVALID")

    invalid_ast_contract = copy.deepcopy(contract_json)
    invalid_ast_contract["precondition"] = {"opvm": {"unknown_op": [{"const": True}]}}
    invalid_ast_contract["contract_id"] = pcs_core.recompute_contract_id(invalid_ast_contract)
    invalid_ast_bundle = build_bundle_json(
        pcs_core,
        contract=invalid_ast_contract,
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
    )
    write_vector(pcs_core, VECTORS / "reject" / "invalid_opvm_ast", invalid_ast_bundle, "REJECT", "SCHEMA_ERROR")

    invocation_header_too_large = copy.deepcopy(accept_bundle)
    invocation_header_too_large["invocation_header"]["nonce_b64"] = "A" * 20000
    invocation_header_too_large["receipt"]["invocation_id"] = pcs_core.recompute_invocation_id(invocation_header_too_large["invocation_header"])
    write_vector(pcs_core, VECTORS / "reject" / "invocation_header_too_large", invocation_header_too_large, "REJECT", "BOUND_VIOLATION")

    merkle_event_proof_invalid = copy.deepcopy(merkle_bundle)
    merkle_event_proof_invalid["receipt"]["included_events"][0]["event_proof"][0]["hash"] = mutate_sha256(
        merkle_event_proof_invalid["receipt"]["included_events"][0]["event_proof"][0]["hash"]
    )
    write_vector(pcs_core, VECTORS / "reject" / "merkle_event_proof_invalid", merkle_event_proof_invalid, "REJECT", "PROOF_INVALID")

    merkle_duplicate_event_index = copy.deepcopy(merkle_bundle)
    merkle_duplicate_event_index["receipt"]["included_events"].append(copy.deepcopy(merkle_duplicate_event_index["receipt"]["included_events"][0]))
    write_vector(pcs_core, VECTORS / "reject" / "merkle_duplicate_event_index", merkle_duplicate_event_index, "REJECT", "PROOF_INVALID")

    chunk_duplicate_pair = copy.deepcopy(merkle_bundle)
    chunk_duplicate_pair["receipt"]["included_chunks"].append(copy.deepcopy(chunk_duplicate_pair["receipt"]["included_chunks"][0]))
    write_vector(pcs_core, VECTORS / "reject" / "chunk_duplicate_pair", chunk_duplicate_pair, "REJECT", "PROOF_INVALID")

    glue_mapping_mismatch = copy.deepcopy(composition_bundle)
    glue_mapping_mismatch["to_bundle"] = build_bundle_json(
        pcs_core,
        contract=copy.deepcopy(contract_json),
        in_value={"task": "echo", "value": 9},
        out_value={"result": 9},
        nonce_b64="BQUFBQUFBQUFBQUFBQUFBQ==",
    )
    glue_mapping_mismatch["glue_receipt"]["to_contract_id"] = glue_mapping_mismatch["to_bundle"]["contract"]["contract_id"]
    glue_mapping_mismatch["glue_receipt"]["to_invocation_id"] = glue_mapping_mismatch["to_bundle"]["receipt"]["invocation_id"]
    write_vector(pcs_core, VECTORS / "reject" / "glue_mapping_mismatch", glue_mapping_mismatch, "REJECT", "INVOCATION_MISMATCH")

    glue_bridge_assert_fail = copy.deepcopy(composition_bundle)
    glue_bridge_assert_fail["glue_receipt"]["bridge_assertions"] = [{"predicate": {"opvm": {"const": False}}}]
    write_vector(pcs_core, VECTORS / "reject" / "glue_bridge_assert_fail", glue_bridge_assert_fail, "REJECT", "ASSERT_FAIL")

    glue_to_invocation_mismatch = copy.deepcopy(composition_bundle)
    glue_to_invocation_mismatch["glue_receipt"]["to_invocation_id"] = mutate_sha256(glue_to_invocation_mismatch["glue_receipt"]["to_invocation_id"])
    write_vector(pcs_core, VECTORS / "reject" / "glue_to_invocation_mismatch", glue_to_invocation_mismatch, "REJECT", "INVOCATION_MISMATCH")

    mixed_trace_modes = copy.deepcopy(merkle_bundle)
    mixed_trace_modes["receipt"]["events"] = [{"kind": "inline"}]
    write_vector(pcs_core, VECTORS / "reject" / "mixed_trace_modes", mixed_trace_modes, "REJECT", "SCHEMA_ERROR")

    assertion_unproven_event = copy.deepcopy(merkle_bundle)
    assertion_unproven_event["receipt"]["assertions"] = [
        build_inline_assertion(
            0,
            {
                "const": True,
            },
        )
    ]
    write_vector(
        pcs_core,
        VECTORS / "reject" / "assertion_unproven_event",
        assertion_unproven_event,
        "REJECT",
        "PROOF_INVALID",
    )

    proof_len_zero_contract = copy.deepcopy(contract_json)
    proof_len_zero_contract["resource_bounds"]["max_proof_len"] = 0
    proof_len_zero_contract["contract_id"] = pcs_core.recompute_contract_id(proof_len_zero_contract)
    proof_len_zero_bundle = build_bundle_json(
        pcs_core,
        contract=proof_len_zero_contract,
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
        receipt_extras=copy.deepcopy(merkle_extras),
    )
    write_vector(
        pcs_core,
        VECTORS / "reject" / "proof_len_exceeded",
        proof_len_zero_bundle,
        "REJECT",
        "BOUND_VIOLATION",
    )

    inclusion_zero_contract = copy.deepcopy(contract_json)
    inclusion_zero_contract["resource_bounds"]["max_inclusion_proofs"] = 0
    inclusion_zero_contract["contract_id"] = pcs_core.recompute_contract_id(inclusion_zero_contract)
    inclusion_zero_bundle = build_bundle_json(
        pcs_core,
        contract=inclusion_zero_contract,
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
        receipt_extras=copy.deepcopy(merkle_extras),
    )
    write_vector(
        pcs_core,
        VECTORS / "reject" / "max_inclusion_proofs_exceeded",
        inclusion_zero_bundle,
        "REJECT",
        "BOUND_VIOLATION",
    )

    proven_bytes_tiny_contract = copy.deepcopy(contract_json)
    proven_bytes_tiny_contract["resource_bounds"]["max_proven_bytes"] = 1
    proven_bytes_tiny_contract["contract_id"] = pcs_core.recompute_contract_id(proven_bytes_tiny_contract)
    proven_bytes_tiny_bundle = build_bundle_json(
        pcs_core,
        contract=proven_bytes_tiny_contract,
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
        receipt_extras=copy.deepcopy(merkle_extras),
    )
    write_vector(
        pcs_core,
        VECTORS / "reject" / "max_proven_bytes_exceeded",
        proven_bytes_tiny_bundle,
        "REJECT",
        "BOUND_VIOLATION",
    )

    short_chunk_entries = build_blob_chunk_entries([b"abc", b"xy"], include_indices=[0])
    short_nonfinal_receipt = build_merkle_trace_extras(pcs_core, merkle_events, include_indices=[1])
    short_nonfinal_receipt["included_chunks"] = short_chunk_entries
    short_nonfinal_bundle = build_bundle_json(
        pcs_core,
        contract=copy.deepcopy(contract_json),
        in_value={"task": "echo", "value": 7},
        out_value={"result": 7},
        nonce_b64=nonce,
        receipt_extras=short_nonfinal_receipt,
    )
    write_vector(
        pcs_core,
        VECTORS / "reject" / "chunk_nonfinal_not_full",
        short_nonfinal_bundle,
        "REJECT",
        "PROOF_INVALID",
    )

    glue_duplicate_to = copy.deepcopy(composition_bundle)
    glue_duplicate_to["glue_receipt"]["mapping"].append({"from": "/in/value", "to": "/in/value"})
    write_vector(pcs_core, VECTORS / "reject" / "glue_duplicate_to_path", glue_duplicate_to, "REJECT", "SCHEMA_ERROR")

    glue_bounds_exceed = copy.deepcopy(composition_bundle)
    glue_bounds_exceed["glue_receipt"]["bounds"]["gas_predicate"] = pcs_core.DEPLOYMENT_PROFILE["max_gas_predicate"] + 1
    write_vector(
        pcs_core,
        VECTORS / "reject" / "glue_bounds_exceed_profile",
        glue_bounds_exceed,
        "REJECT",
        "BOUND_VIOLATION",
    )

    glue_to_path_invalid = copy.deepcopy(composition_bundle)
    glue_to_path_invalid["glue_receipt"]["mapping"][0]["to"] = "/out/value"
    write_vector(pcs_core, VECTORS / "reject" / "glue_to_path_invalid", glue_to_path_invalid, "REJECT", "SCHEMA_ERROR")

    write_vector(pcs_core, VECTORS / "reject" / "duplicate_key_bundle", '{"contract":{},"contract":{}}', "REJECT", "PARSE_ERROR")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
