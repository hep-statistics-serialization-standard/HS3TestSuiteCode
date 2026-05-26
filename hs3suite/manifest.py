from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


TYPE_SECTIONS = ("distributions", "functions", "data", "domains", "likelihoods")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(path: Path) -> str:
    payload = load_json(path)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def extract_features(payload: dict[str, Any]) -> dict[str, list[str]]:
    sections = sorted(key for key, value in payload.items() if isinstance(value, list))
    types: set[str] = set()
    for section in TYPE_SECTIONS:
        for item in payload.get(section, []) or []:
            if isinstance(item, dict) and isinstance(item.get("type"), str):
                types.add(item["type"])
    return {"sections": sections, "types": sorted(types), "semantic": []}


def verify_hashes(root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for fixture in manifest["fixtures"]:
        path = root / fixture["path"] / "hs3.json"
        expected = fixture["hashes"]
        actual_raw = raw_sha256(path)
        actual_canonical = canonical_sha256(path)
        if actual_raw != expected["sha256"]:
            errors.append(f"{fixture['test_id']}: raw sha256 mismatch")
        if actual_canonical != expected["canonical_sha256"]:
            errors.append(f"{fixture['test_id']}: canonical sha256 mismatch")
    return errors
