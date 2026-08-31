"""Upconvert HS3 files by round-tripping them through a backend.

The conversion itself belongs to the backend plugin: only it knows how to import an
HS3 file into its own object model, re-export it, and keep its library quiet while
doing so. This script therefore decides *which* files to convert, where the result
goes, and what to report — and calls ``backend.upconvert`` for each one. See
``docs/reference-backend-contract.md``.
"""

from __future__ import annotations

import argparse
from functools import partial
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable


CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))

from hs3suite.backends import (  # noqa: E402
    DEFAULT_CLASS,
    DEFAULT_MODULE,
    ENV_VAR,
    backend_name,
    build_backend,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upconvert HS3 JSON files by importing and re-exporting them with a backend."
    )
    parser.add_argument(
        "hs3_files",
        nargs="*",
        type=Path,
        help="HS3 JSON files to upconvert. Defaults to every fixture hs3.json in manifest.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Write upconverted files under this directory instead of overwriting in place.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Let the backend print its own diagnostics (RooFit's, for the RooFit image).",
    )
    parser.add_argument(
        "--root",
        "-r",
        type=Path,
        default=Path.cwd(),
        help="suite root holding fixtures/ and manifest.json (default: current directory)",
    )
    parser.add_argument(
        "--backend",
        "-b",
        default=None,
        help=(
            "backend plugin as 'module' or 'module:Class'; must expose upconvert "
            f"(default: ${ENV_VAR}, else {DEFAULT_MODULE}:{DEFAULT_CLASS})"
        ),
    )
    return parser


def discover_hs3_files(root: Path, explicit_files: Iterable[Path]) -> list[Path]:
    files = [path.resolve() for path in explicit_files]
    if files:
        return files

    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        return [
            (root / fixture["path"] / "hs3.json").resolve()
            for fixture in manifest.get("fixtures", [])
        ]

    return sorted((root / "fixtures").glob("*/hs3.json"))


def output_path_for(input_path: Path, root: Path, output_dir: Path | None) -> Path:
    if output_dir is None:
        return input_path

    try:
        relative_path = input_path.relative_to(root)
    except ValueError:
        relative_path = Path(input_path.name)
    return output_dir / relative_path


def backend_upconvert(backend: Any, spec: str | None = None) -> Any:
    """The plugin's ``upconvert``, or a clear error if it ships none.

    Upconversion is optional in the backend contract — a plugin that only runs checks
    is still a valid plugin — so a missing method is a usage error, not a crash.
    """
    upconvert = getattr(backend, "upconvert", None)
    if not callable(upconvert):
        raise SystemExit(
            f"backend {backend_name(backend, spec)!r} implements no "
            "upconvert(input_path, output_path); see docs/reference-backend-contract.md."
        )
    return upconvert


def accepts_verbose(upconvert: Any) -> bool:
    """Whether ``upconvert`` takes the optional ``verbose`` keyword.

    Asked up front rather than by catching ``TypeError`` around the call, which would
    also swallow a ``TypeError`` raised inside the backend.
    """
    try:
        parameters = inspect.signature(upconvert).parameters
    except (TypeError, ValueError):  # C callables need not be introspectable
        return False
    return "verbose" in parameters or any(
        parameter.kind is parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def upconvert_hs3_file(upconvert: Any, input_path: Path, output_path: Path) -> None:
    """Run ``upconvert`` and move its output into place.

    The backend writes into a staging directory and the result is moved over
    ``output_path`` only once it returns, so a conversion that fails half-way cannot
    truncate a fixture — the default is to overwrite in place.
    """
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Staged next to the target so the move is atomic, and under the target's own name
    # so a backend that picks its output format from the extension still sees `.json`.
    with tempfile.TemporaryDirectory(dir=output_path.parent) as staging:
        temp_path = Path(staging) / output_path.name
        upconvert(input_path, temp_path)
        if not temp_path.is_file():
            raise RuntimeError("backend upconvert wrote no output file")
        os.replace(temp_path, output_path)


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else None
    hs3_files = discover_hs3_files(root, args.hs3_files)

    if not hs3_files:
        print("No HS3 files found.", file=sys.stderr)
        return 1

    backend = build_backend(args.backend)
    upconvert = backend_upconvert(backend, args.backend)
    if accepts_verbose(upconvert):
        upconvert = partial(upconvert, verbose=args.verbose)
    elif args.verbose:
        # Verbosity is optional in the contract: say so rather than pretending it took.
        print(
            f"note: backend {backend_name(backend, args.backend)!r} takes no verbose "
            "flag; its output is whatever it prints by default.",
            file=sys.stderr,
        )
    failures: list[tuple[Path, Exception]] = []

    for input_path in hs3_files:
        output_path = output_path_for(input_path, root, output_dir)
        try:
            upconvert_hs3_file(upconvert, input_path, output_path)
        except Exception as exc:
            failures.append((input_path, exc))
            print(f"FAILED {display_path(input_path, root)}: {exc}", file=sys.stderr)
            continue

        source = display_path(input_path, root)
        target = display_path(output_path, root)
        if source == target:
            print(f"UPDATED {source}")
        else:
            print(f"UPDATED {source} -> {target}")

    if failures:
        print(f"Failed to upconvert {len(failures)} of {len(hs3_files)} HS3 file(s).", file=sys.stderr)
        return 1

    print(f"Upconverted {len(hs3_files)} HS3 file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
