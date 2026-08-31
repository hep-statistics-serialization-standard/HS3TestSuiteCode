from __future__ import annotations

import argparse
from pathlib import Path

from .backends import (
    DEFAULT_CLASS,
    DEFAULT_MODULE,
    ENV_VAR,
    backend_name,
    build_backend,
)
from .runner import run_suite, summarize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hs3suite")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run HS3 fixture checks")
    run.add_argument(
        "--backend",
        default=None,
        help=(
            "backend plugin as 'module' or 'module:Class' "
            f"(default: ${ENV_VAR}, else {DEFAULT_MODULE}:{DEFAULT_CLASS})"
        ),
    )
    run.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="suite root holding fixtures/ and manifest.json (default: current directory)",
    )
    run.add_argument("--test-id", action="append", help="run only this fixture id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        selected = set(args.test_id) if args.test_id else None
        try:
            backend = build_backend(args.backend)
        except RuntimeError as exc:
            print(f"ERROR   {exc}")
            return 2
        print(f"Backend: {backend_name(backend, args.backend)}")
        results = run_suite(args.root.resolve(), args.backend, selected, backend=backend)
        for result in results:
            label = f"{result.test_id}::{result.check_id}"
            detail = f" - {result.message}" if result.message else ""
            print(f"{result.status.upper():7} {label}{detail}")
        summary = summarize(results)
        print(
            "Summary: "
            + ", ".join(f"{key}={summary.get(key, 0)}" for key in ("passed", "xfail", "skipped", "failed"))
        )
        return 1 if summary.get("failed", 0) else 0
    return 2
