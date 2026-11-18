"""The optional "parameter_point" of scan checks, with a synthetic suite and a fake backend."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from hs3suite.manifest import canonical_sha256, raw_sha256
from hs3suite.runner import run_suite
from hs3suite.specs import FixtureSpec, NLLScanSpec
from hs3suite.validation import validate_suite

CODE_ROOT = Path(__file__).resolve().parents[1]

HS3 = {
    "metadata": {"hs3_version": "0.2"},
    "parameter_points": [
        {"name": "default_values", "parameters": [{"name": "a", "value": 1.0}, {"name": "b", "value": 2.0}]},
        {"name": "other", "parameters": [{"name": "b", "value": 5.0, "const": True}]},
    ],
}


class FakeBackend:
    """Records which workspace, in which parameter point, every scan ran on."""

    name = "fake"

    def __init__(self) -> None:
        self.imports = 0
        self.scans: list[tuple[str, str | None, int]] = []

    def load_workspace(self, path: Path) -> dict:
        self.imports += 1
        return {"point": None, "import": self.imports}

    def run_structure_check(self, workspace: dict, check: dict) -> None:
        pass

    def apply_parameter_point(self, workspace: dict, name: str, hs3_path: Path) -> None:
        if name not in ("default_values", "other"):
            raise AssertionError(f"parameter point {name!r} not found")
        assert workspace["point"] is None, "a parameter point is loaded into a fresh import"
        workspace["point"] = name

    def run_twice_delta_nll_scan(self, workspace: dict, check: dict, hs3_path: Path) -> list[float]:
        self.scans.append((check["id"], workspace["point"], workspace["import"]))
        return [0.0]


class BackendWithoutParameterPoints(FakeBackend):
    apply_parameter_point = None


def scan(check_id: str, parameter_point: str | None = None) -> dict:
    check = {
        "id": check_id,
        "kind": "twice_delta_nll_scan",
        "target": {"pdf": "model", "data": "obs"},
        "reference_point": {"a": 1.0},
        "scan_parameters": ["a"],
        "scan_points": [[1.0]],
        "expected": [0.0],
        "tolerance": {"rtol": 0.0, "atol": 1e-12},
    }
    if parameter_point is not None:
        check["parameter_point"] = parameter_point
    return check


def write_suite(root: Path, checks: list[dict]) -> None:
    fixture = root / "fixtures" / "synthetic"
    fixture.mkdir(parents=True)
    (fixture / "hs3.json").write_text(json.dumps(HS3))
    metadata = {
        "schema_version": 1,
        "test_id": "synthetic",
        "title": "synthetic",
        "source": "none",
        "description": "synthetic",
        "notes": [],
        "nll_scans": [{"id": "from_other", "pdf": "model", "data": "obs", "parameters": ["a"], "points": [[1.0]], "parameter_point": "other"}],
    }
    (fixture / "metadata.json").write_text(json.dumps(metadata))
    expected = {"schema_version": 1, "test_id": "synthetic", "checks": checks}
    (fixture / "expected.json").write_text(json.dumps(expected))
    manifest = {
        "schema_version": 1,
        "fixtures": [
            {
                "test_id": "synthetic",
                "path": "fixtures/synthetic",
                "hashes": {
                    "sha256": raw_sha256(fixture / "hs3.json"),
                    "canonical_sha256": canonical_sha256(fixture / "hs3.json"),
                },
                "features": {"sections": [], "types": [], "semantic": []},
                "tags": [],
                "conformance": [],
                "checks": [check["id"] for check in checks],
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))


CHECKS = [
    {"id": "structure_import", "kind": "structure_import", "target": {}},
    scan("default_first"),
    scan("other_1", "other"),
    scan("other_2", "other"),
    scan("default_again"),
    scan("explicit_default", "default_values"),
]


def test_schemas_accept_parameter_point(tmp_path: Path) -> None:
    write_suite(tmp_path, CHECKS)
    assert validate_suite(tmp_path) == []


def test_runner_loads_parameter_points_into_fresh_imports(tmp_path: Path) -> None:
    write_suite(tmp_path, CHECKS)
    backend = FakeBackend()
    results = run_suite(tmp_path, backend=backend)
    assert [result.status for result in results] == ["passed"] * len(CHECKS)
    # The checks without the key run from the state after import; a change of the parameter
    # point costs one import, consecutive checks from the same point share theirs.
    assert backend.scans == [
        ("default_first", None, 1),
        ("other_1", "other", 1),
        ("other_2", "other", 1),
        ("default_again", None, 2),
        ("explicit_default", "default_values", 2),
    ]


def test_unknown_parameter_point_fails_the_check(tmp_path: Path) -> None:
    write_suite(tmp_path, [scan("unknown", "nowhere"), scan("default")])
    results = {result.check_id: result for result in run_suite(tmp_path, backend=FakeBackend())}
    assert results["unknown"].status == "failed"
    assert "nowhere" in results["unknown"].message
    assert results["default"].status == "passed"


def test_backend_without_parameter_points_fails_instead_of_ignoring_the_key(tmp_path: Path) -> None:
    write_suite(tmp_path, [scan("default"), scan("other", "other")])
    backend = BackendWithoutParameterPoints()
    results = {result.check_id: result for result in run_suite(tmp_path, backend=backend)}
    assert results["default"].status == "passed"
    assert results["other"].status == "failed"
    assert "apply_parameter_point" in results["other"].message
    assert [check_id for check_id, _, _ in backend.scans] == ["default"]


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "build_manifest_and_expected", CODE_ROOT / "tools" / "build_manifest_and_expected.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generator_writes_parameter_point_and_its_reference_point(tmp_path: Path) -> None:
    generator = load_generator()
    write_suite(tmp_path, CHECKS)
    fixture_dir = tmp_path / "fixtures" / "synthetic"
    metadata = json.loads((fixture_dir / "metadata.json").read_text())
    scans = generator._specs(metadata, "nll_scans", NLLScanSpec) + (
        NLLScanSpec.one_dim("model", "obs", "a", (1.0,), id="from_default"),
    )
    assert scans[0].parameter_point == "other"
    spec = FixtureSpec("synthetic", "synthetic", "none", "synthetic", scans, "fake")
    backend = FakeBackend()
    expected = generator.build_expected(spec, HS3, backend, fixture_dir)
    by_id = {check["id"]: check for check in expected["checks"]}
    # The named point is laid over "default_values".
    assert by_id["from_other"]["parameter_point"] == "other"
    assert by_id["from_other"]["reference_point"] == {"a": 1.0, "b": 5.0}
    assert "parameter_point" not in by_id["from_default"]
    assert by_id["from_default"]["reference_point"] == {"a": 1.0, "b": 2.0}
    assert backend.scans == [("from_other", "other", 1), ("from_default", None, 2)]
