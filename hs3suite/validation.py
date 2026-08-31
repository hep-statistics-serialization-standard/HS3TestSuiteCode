from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from .manifest import load_json


SCHEMAS = {
    "manifest": "manifest.schema.json",
    "metadata": "metadata.schema.json",
    "expected": "expected.schema.json",
}


def load_schema(name: str) -> Any:
    """Load a bundled JSON schema by short name ("manifest", "metadata", ...)."""
    try:
        filename = SCHEMAS[name]
    except KeyError:
        raise ValueError(f"unknown schema {name!r}") from None
    resource = resources.files(__package__).joinpath("schemas", filename)
    return json.loads(resource.read_text(encoding="utf-8"))


def validate_suite(root: Path) -> list[str]:
    errors: list[str] = []
    schemas = {name: load_schema(name) for name in SCHEMAS}
    manifest = load_json(root / "manifest.json")
    try:
        jsonschema.validate(manifest, schemas["manifest"])
    except jsonschema.ValidationError as exc:
        return [f"manifest.json: {exc.message}"]

    for fixture in manifest["fixtures"]:
        fixture_dir = root / fixture["path"]
        hs3_path = fixture_dir / "hs3.json"
        if not hs3_path.exists():
            errors.append(f"{hs3_path.relative_to(root)}: file not found")
        for kind in ("metadata", "expected"):
            path = fixture_dir / f"{kind}.json"
            if not path.exists():
                errors.append(f"{path.relative_to(root)}: file not found")
                continue
            payload = load_json(path)
            try:
                jsonschema.validate(payload, schemas[kind])
            except jsonschema.ValidationError as exc:
                errors.append(f"{path.relative_to(root)}: {exc.message}")
            if kind == "expected":
                errors.extend(_duplicate_check_id_errors(path, root, payload))
    return errors


def _duplicate_check_id_errors(path: Path, root: Path, payload: dict) -> list[str]:
    ids = [check["id"] for check in payload.get("checks", []) if "id" in check]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if not dupes:
        return []
    return [f"{path.relative_to(root)}: duplicate check id {dup!r}" for dup in dupes]
