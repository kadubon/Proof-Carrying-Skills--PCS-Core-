from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VECTOR_ROOT = ROOT / "test-vectors"
VERIFIER = ROOT / "reference-checker" / "verifier.py"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _json_pointer(parts) -> str:
    encoded = [str(p).replace("~", "~0").replace("/", "~1") for p in parts]
    return "/" + "/".join(encoded)


def _schema_error_key(err) -> tuple[str, str, str]:
    return (_json_pointer(err.absolute_path), str(err.validator), str(err.message))


def run_bundle_vector(vector_dir: Path, expected: dict) -> tuple[bool, str]:
    bundle_path = vector_dir / "bundle.json"
    if not bundle_path.exists():
        return False, f"{vector_dir}: missing bundle.json"

    runs: list[dict] = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, str(VERIFIER), "--bundle", str(bundle_path)],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode not in (0, 2):
            return False, f"{vector_dir}: verifier failed with code {proc.returncode}: {proc.stderr.strip()}"
        try:
            runs.append(json.loads(proc.stdout))
        except json.JSONDecodeError as exc:
            return False, f"{vector_dir}: verifier output is not json: {exc}"

    if runs[0] != runs[1]:
        return False, f"{vector_dir}: non-deterministic result across repeated runs"

    actual = runs[0]
    for field in ("decision", "rejection_code", "decision_sha256"):
        if field in expected and actual.get(field) != expected.get(field):
            return False, f"{vector_dir}: {field} mismatch expected={expected.get(field)} actual={actual.get(field)}"
    return True, f"{vector_dir}: PASS (bundle)"


def run_schema_vector(vector_dir: Path, expected: dict) -> tuple[bool, str]:
    instance_path = vector_dir / "instance.json"
    if not instance_path.exists():
        return False, f"{vector_dir}: missing instance.json"

    schema_rel = expected.get("schema")
    if not isinstance(schema_rel, str) or not schema_rel:
        return False, f"{vector_dir}: expected.schema must be a non-empty string"

    schema_path = ROOT / schema_rel
    if not schema_path.exists():
        return False, f"{vector_dir}: schema not found: {schema_path}"

    if "valid" not in expected:
        return False, f"{vector_dir}: expected.valid is required for schema vectors"

    try:
        from jsonschema import Draft202012Validator
    except Exception as exc:
        return False, f"{vector_dir}: jsonschema is required for schema vectors: {exc}"

    instance = load_json(instance_path)
    schema = load_json(schema_path)

    runs: list[dict] = []
    for _ in range(2):
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(instance), key=_schema_error_key)
        first_error = None
        if errors:
            first = errors[0]
            first_error = {
                "path": _json_pointer(first.absolute_path),
                "validator": str(first.validator),
                "message": str(first.message),
            }
        runs.append(
            {
                "valid": len(errors) == 0,
                "error_count": len(errors),
                "first_error": first_error,
            }
        )

    if runs[0] != runs[1]:
        return False, f"{vector_dir}: non-deterministic schema result across repeated runs"

    actual = runs[0]
    expected_valid = bool(expected["valid"])
    if actual["valid"] != expected_valid:
        return False, f"{vector_dir}: valid mismatch expected={expected_valid} actual={actual['valid']}"

    if "error_count" in expected and actual["error_count"] != expected["error_count"]:
        return False, (
            f"{vector_dir}: error_count mismatch "
            f"expected={expected['error_count']} actual={actual['error_count']}"
        )

    if "first_error_path" in expected:
        path = actual["first_error"]["path"] if actual["first_error"] else None
        if path != expected["first_error_path"]:
            return False, f"{vector_dir}: first_error_path mismatch expected={expected['first_error_path']} actual={path}"

    if "first_error_validator" in expected:
        validator = actual["first_error"]["validator"] if actual["first_error"] else None
        if validator != expected["first_error_validator"]:
            return False, (
                f"{vector_dir}: first_error_validator mismatch "
                f"expected={expected['first_error_validator']} actual={validator}"
            )

    return True, f"{vector_dir}: PASS (schema)"


def run_one(vector_dir: Path) -> tuple[bool, str]:
    expected_path = vector_dir / "expected.json"
    if not expected_path.exists():
        return False, f"{vector_dir}: missing expected.json"

    expected = load_json(expected_path)
    mode = expected.get("mode", "bundle")

    if mode == "bundle":
        return run_bundle_vector(vector_dir, expected)
    if mode == "schema":
        return run_schema_vector(vector_dir, expected)
    return False, f"{vector_dir}: unsupported mode={mode}"


def main() -> int:
    vector_dirs = sorted(p.parent for p in VECTOR_ROOT.rglob("expected.json") if p.parent != VECTOR_ROOT)
    if not vector_dirs:
        print("No vectors found.")
        return 1

    failed = 0
    for vector_dir in vector_dirs:
        ok, message = run_one(vector_dir)
        print(message)
        if not ok:
            failed += 1

    print(f"total={len(vector_dirs)} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())