from __future__ import annotations

import argparse
import json
from pathlib import Path

import pcs_core


def main() -> int:
    parser = argparse.ArgumentParser(description="PCS-Core minimal deterministic verifier")
    parser.add_argument("--bundle", required=True, help="Path to JSON bundle")
    args = parser.parse_args()

    raw = Path(args.bundle).read_bytes()
    result = pcs_core.verify_bundle_bytes(raw)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"] == "ACCEPT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
