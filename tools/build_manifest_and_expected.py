from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))

try:
    from hs3suite_backend import HS3TestSuiteBackend
except:
    print("module 'hs3suite_backend' not found. Falling back to builtin RooFitBackend!")
    from hs3suite.backends.roofit import RooFitBackend as HS3TestSuiteBackend

from hs3suite.manifest import (
    canonical_sha256,
    extract_features,
    load_json,
    raw_sha256,
    write_json,
)
from hs3suite.specs import FIXTURES as LEGACY_FIXTURES
from hs3suite.specs import FixtureSpec, FunctionScanSpec, NLLScanSpec, PdfScanSpec


def relative_path(path: Path, root: Path) -> str:
    """Manifest-style path of ``path``, always relative to the suite root."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        raise SystemExit(f"fixture {path} is outside the suite root {root}") from None


def default_parameter_point(payload: dict[str, Any]) -> dict[str, float]:
    for point in payload.get("parameter_points", []):
        if point.get("name") == "default_values":
            return {
                parameter["name"]: float(parameter["value"])
                for parameter in point.get("parameters", [])
                if "name" in parameter and "value" in parameter
            }
    return {}


def structure_targets(payload: dict[str, Any]) -> dict[str, list[str]]:
    def public_function_name(item: dict[str, Any]) -> bool:
        name = item.get("name", "")
        return isinstance(name, str) and not name.endswith("_exponential_inverted")

    return {
        "pdfs": sorted(
            item["name"] for item in payload.get("distributions", []) if "name" in item
        ),
        "functions": sorted(
            item["name"]
            for item in payload.get("functions", [])
            if "name" in item and public_function_name(item)
        ),
        "data": sorted(
            item["name"] for item in payload.get("data", []) if "name" in item
        ),
    }


def comparison() -> dict[str, str]:
    return {
        "type": "pointwise_comp",
        "rule": "|evaluated - expected| <= atol + rtol * |expected|",
    }


def build_expected(
    spec, payload: dict[str, Any], backend: None, fixture_dir: Path
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        {"id": "static_integrity", "kind": "static_integrity"},
        {
            "id": "structure_import",
            "kind": "structure_import",
            "target": structure_targets(payload),
        },
    ]
    is_roofit_xfail = bool(
        spec.backend_expectations.get("roofit", {}).get("xfail", False)
    )
    scans = spec.all_scans()
    if scans and not is_roofit_xfail:
        if backend is None:
            raise RuntimeError("RooFit backend is required to build numeric expectations")
        hs3_path = fixture_dir / "hs3.json"
        workspace = backend.load_workspace(hs3_path)
        for scan in scans:
            check = {
                "id": scan.id,
                "kind": scan.kind,
                "target": scan.target(),
                "reference_point": default_parameter_point(payload),
                "scan_parameters": list(scan.parameters),
                "scan_points": [list(point) for point in scan.points],
                "expected": [],
                "tolerance": scan.tolerance(),
                "comparison": comparison(),
            }
            # Each kind's backend entry point is run_<kind> by construction.
            run = getattr(backend, f"run_{scan.kind}")
            check["expected"] = run(workspace, check, hs3_path)
            checks.append(check)
    return {"schema_version": 1, "test_id": spec.test_id, "checks": checks}


def build_metadata(spec) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "test_id": spec.test_id,
        "title": spec.title,
        "source": spec.source,
        "description": spec.description,
        "notes": list(spec.notes),
        "modified_from_source": spec.modified_from_source,
        "reference_backend": "roofit 6.41.01",
    }


def build_manifest_entry(
    spec, payload: dict[str, Any], fixture_dir: Path, root: Path
) -> dict[str, Any]:
    hs3_path = fixture_dir / "hs3.json"
    features = extract_features(payload)
    features["semantic"] = sorted(spec.semantic)
    expected = load_json(fixture_dir / "expected.json")
    entry = {
        "test_id": spec.test_id,
        "path": relative_path(fixture_dir, root),
        "hashes": {
            "sha256": raw_sha256(hs3_path),
            "canonical_sha256": canonical_sha256(hs3_path),
        },
        "features": features,
        "tags": sorted(spec.tags),
        "conformance": sorted(spec.conformance),
        "checks": [check["id"] for check in expected["checks"]],
    }
    if spec.backend_expectations:
        entry["backend_expectations"] = spec.backend_expectations
    return entry


def _specs(md: dict[str, Any], key: str, cls) -> tuple[Any, ...]:
    """Translate a metadata scan list into specs of ``cls``.

    Ids default to the class default for a single entry and are suffixed otherwise, so a
    fixture with one scan gets a clean ``pdf_scan`` rather than ``pdf_scan_0``.
    """
    entries = md.get(key, ())
    default_id = cls.id
    return tuple(
        cls(
            parameters=tuple(entry["parameters"]),
            points=tuple(tuple(point) for point in entry["points"]),
            id=entry.get("id", default_id if len(entries) == 1 else f"{default_id}_{i}"),
            **{
                field: (tuple(entry[field]) if field == "observables" else entry[field])
                for field in ("pdf", "data", "likelihood", "observables", "function")
                if field in entry
            },
        )
        for i, entry in enumerate(entries)
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixtures",
        "-f",
        nargs="+",
        default=[],
        type=Path,
        help="fixture directories to build (default: all legacy specs under <root>/fixtures)",
    )
    parser.add_argument("--extend-manifest", "-e", action="store_true")
    parser.add_argument(
        "--root",
        "-r",
        type=Path,
        default=Path.cwd(),
        help="suite root holding fixtures/ and manifest.json (default: current directory)",
    )

    args = parser.parse_args()
    root = args.root.resolve()
    backend = HS3TestSuiteBackend()

    # (spec, fixture directory) pairs; the directory is where hs3.json and the generated
    # metadata.json/expected.json live.
    FIXTURES: list[tuple[Any, Path]] = []
    if len(args.fixtures) > 0:
        print("generate fixtures for the test cases:")
        print("\n".join(str(path) for path in args.fixtures))
        for testcase_path in args.fixtures:
            fixture_dir = (root / testcase_path).resolve()
            md = load_json(fixture_dir / "metadata.json")
            md["nll_scans"] = _specs(md, "nll_scans", NLLScanSpec)
            md["pdf_scans"] = _specs(md, "pdf_scans", PdfScanSpec)
            md["function_scans"] = _specs(md, "function_scans", FunctionScanSpec)
            spec = FixtureSpec(
                test_id=md["test_id"],
                title=md["title"],
                source=md["source"],
                description=md["description"],
                nll_scans=md.get("nll_scans", ()),
                pdf_scans=md.get("pdf_scans", ()),
                function_scans=md.get("function_scans", ()),
                semantic=md.get("semantic", ()),
                tags=md.get("tags", ()),
                conformance=md.get("conformance", ()),
                modified_from_source=md["modified_from_source"],
                notes=md.get("notes", ""),
                backend_expectations=md.get("backend_expectations", {}),
                reference_backend=md["reference_backend"],
            )
            FIXTURES.append((spec, fixture_dir))
    else:
        FIXTURES = [
            (spec, root / "fixtures" / spec.test_id) for spec in LEGACY_FIXTURES
        ]
    for spec, fixture_dir in FIXTURES:
        payload = load_json(fixture_dir / "hs3.json")
        metadata_path = fixture_dir / "metadata.json"
        if not metadata_path.exists():
            write_json(metadata_path, build_metadata(spec))
        write_json(
            fixture_dir / "expected.json",
            build_expected(spec, payload, backend, fixture_dir),
        )

    manifest_path = root / "manifest.json"
    if args.extend_manifest and manifest_path.exists():
        manifest = load_json(manifest_path)
        manifest["schema_version"] = 1
        manifest["generated_by"] = "tools/build_manifest_and_expected.py"
    else:
        manifest = {
            "schema_version": 1,
            "generated_by": "tools/build_manifest_and_expected.py",
            "fixtures": [],
        }

    entries = {entry["test_id"]: entry for entry in manifest["fixtures"]}
    for spec, fixture_dir in FIXTURES:
        payload = load_json(fixture_dir / "hs3.json")
        entries[spec.test_id] = build_manifest_entry(spec, payload, fixture_dir, root)
    manifest["fixtures"] = list(entries.values())
    write_json(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
