# Reference-backend generation contract

This document specifies what a reference-backend Docker image must provide so the
`generate` job in `.github/workflows/update-fixtures.yaml` can use it to generate a
*new* fixture's `expected.json` and manifest entry when a PR adds
`fixtures/<test_id>/hs3.json` + `metadata.json` and is labeled `generate fixtures`.
This flow is for onboarding new fixtures only — it never re-generates or mutates an
already-committed fixture.

It exists so that adding a new reference backend never requires touching this
workflow, and never requires the backend author to write Python. The Python
pipeline (`tools/build_manifest_and_expected.py`, driven by a `hs3suite_backend`
plugin — see *The `hs3suite_backend` plugin seam* below) remains the default,
blessed path for backend authors who are fine writing Python. This contract
exists for everyone else.

## Picking up an image

`update-fixtures.yaml`'s `determine-jobs` job reads each new fixture's
`fixtures/<test_id>/metadata.json`, and uses its `reference_backend` field directly as
the container image reference (`matrix.image`) for the `generate` job. That field must
therefore be a real, pullable image reference (e.g.
`ghcr.io/your-org/hs3testsuite-mybackend:1.0`), not just a human-readable label.

## The `generate-fixtures` executable

The image must expose an executable named `generate-fixtures` on `$PATH`.

GitHub Actions' `jobs.<job>.container` never invokes the image's `ENTRYPOINT` or
`CMD` — it starts the container, keeps it alive, and runs each workflow step via
`docker exec`. So `generate-fixtures` must be a real, discoverable executable, not
something wired up only through the image's default command.

**Invocation:**

```
generate-fixtures <absolute-path-to-fixture-dir>
```

Called as, e.g.:

```
generate-fixtures "$GITHUB_WORKSPACE/fixtures/rf101_basics"
```

**Environment:** the step runs inside the already-checked-out PR branch. Working
directory is the repo root (`$GITHUB_WORKSPACE`). `<fixture-dir>/hs3.json` and
`<fixture-dir>/metadata.json` already exist on disk (the PR that triggers this flow
adds both).

**Required effects on success:**

- `<fixture-dir>/expected.json` is written, valid against
  `hs3suite/schemas/expected.schema.json`.
- `<repo-root>/manifest.json` is *extended* (not replaced) with this fixture's entry,
  valid against `hs3suite/schemas/manifest.schema.json` — including correct
  `hashes.sha256` / `hashes.canonical_sha256` for `hs3.json` (see `raw_sha256` /
  `canonical_sha256` in `hs3suite/manifest.py`).

A non-zero exit code fails the `generate` job and nothing is committed.

**Which checks to emit:** driven by `metadata.json`. An `nll_scans` entry produces a
`twice_delta_nll_scan` check; `pdf_scans` and `function_scans` entries produce `pdf_scan`
and `function_scan` checks. A backend image is expected to support all three. The two
data-less kinds matter in particular for fixtures whose `hs3.json` has no `data` section
at all, where `twice_delta_nll_scan` cannot be built — without them such a fixture pins
no numbers.

For `pdf_scan`, the value written to `expected` must be the density **normalised over the
observables named in the entry**, not a raw expression value. Backends might disagree about what an unnormalised pdf value means, which is the same
reason `twice_delta_nll_scan` freezes `2ΔNLL` rather than a raw NLL.

## The `hs3suite_backend` plugin seam

Independently of `generate-fixtures`, an image may install a Python module named
`hs3suite_backend` on `PYTHONPATH` exposing a class `HS3TestSuiteBackend`. That is the
seam both the fixture generator and the **runner** (`python -m hs3suite run`) load their
backend through, so one plugin serves both: generating a fixture's frozen values, and
checking any backend against them. `--backend module:Class` and `$HS3SUITE_BACKEND`
override the default for local experiments.

The class implements `load_workspace`, `structure`, `run_structure_check`,
`run_twice_delta_nll_scan`, `run_pdf_scan` and `run_function_scan`, and carries a `name`
attribute. `name` is the key under which `manifest.json` records this backend's
`backend_expectations` (`xfail`/`reason`) — it is the backend's identity in the suite,
so it must stay stable across image rebuilds, and it is what the runner reports.

### `upconvert` (optional)

```python
def upconvert(self, input_path: Path, output_path: Path, *, verbose: bool = False) -> None
```

Read the HS3 file at `input_path`, import it, re-export it, and write the result to
`output_path` — the round-trip that migrates a fixture to whatever HS3 revision the
backend now emits. It backs `tools/upconvert_hs3.py`, which finds the files to convert
and moves each result into place; the conversion itself is the plugin's business,
including keeping its library's diagnostics quiet. A plugin that only runs checks may
omit it — the tool then exits with a message naming the backend, rather than failing
mid-run.

`output_path` is a fresh path inside a staging directory, named exactly like the file
it will replace, so writing it cannot damage the fixture; the tool moves it over the
target only once `upconvert` returns. Raise on failure — a backend that returns
normally without writing `output_path` is reported as a failed conversion.

`verbose` carries the tool's `--verbose` flag: with it set, let the library print
whatever it normally would, since a failed import is otherwise silent. The keyword is
optional — the tool inspects the signature and omits it for a plugin that takes only
the two paths, warning that `--verbose` will not take effect.

For RooFit that is:

```python
def upconvert(
    self, input_path: Path, output_path: Path, *, verbose: bool = False
) -> None:
    workspace = self.ROOT.RooWorkspace("hs3suite_upconvert_ws")
    tool = self.ROOT.RooJSONFactoryWSTool(workspace)
    with nullcontext() if verbose else suppress_root_output():
        if not tool.importJSON(str(input_path)):
            raise RuntimeError(f"RooFit importJSON failed for {input_path}")
        if not tool.exportJSON(str(output_path)):
            raise RuntimeError(f"RooFit exportJSON failed for {output_path}")
```

The module has to work standing alone: it is installed as a top-level module, so it must
not import from the `hs3suite` package (no relative imports), and its own dependencies
are its own problem. Naming matters — a module or class under a different name is not
found, and the loader will say exactly that rather than silently substituting anything.

For the RooFit reference image, `generate-fixtures` is a thin wrapper that sources the
ROOT environment and execs
`python3 "$GITHUB_WORKSPACE/.hs3suite/tools/build_manifest_and_expected.py" -e -f "$1"`
— i.e. it delegates to this repo's existing Python pipeline, checked out alongside the
fixtures. The tool treats the working directory as the suite root (override with
`--root`), so `fixtures/` and `manifest.json` are resolved under `$GITHUB_WORKSPACE`
rather than next to the script. A backend author working in another
language instead ships a self-contained `generate-fixtures` that reproduces the same
effects above without depending on this repo's Python at all.

## Validating a backend container (not yet designed)

There is currently no automated check that a non-RooFit `generate-fixtures`
implementation produces numerically correct output — schema-valid isn't the same as
correct. Cross-checking each new submission against RooFit was considered and
rejected: RooFit can't import every HS3 feature, so it can't serve as ground truth for
fixtures that specifically exercise features RooFit doesn't support, which is likely
to be exactly why a non-RooFit backend was used in the first place.

The direction being considered instead is validating the *backend container itself*,
independent of any specific new fixture: run a candidate `generate-fixtures` image
against a fixed set of existing fixtures that are tagged as using only HS3 "core"
features (features every conformant backend is expected to support), and check
agreement there. This requires fixtures to be mapped to conformance classes first,
which doesn't exist yet (see `conformance`/`semantic` fields in `hs3suite/specs.py` and
`features` in `manifest.json` for the current, incomplete building blocks). Until that
mapping exists, there is no automated backend-container validation — new
non-RooFit-generated fixtures are trusted on review, not gated by CI.
