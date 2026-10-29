"""Regression tests for baport: port each case and compare with what it ported to before.

Each directory in tests/cases holds:
    input.py     the plugin before porting
    args         optional, baport's arguments for it (default: -t client)
    expected.py  what baport made of it
    notes.txt    what baport printed

Usage:
    python tests/run.py               run every case
    python tests/run.py autorun api8  only cases whose name contains one of these
    python tests/run.py --update      rewrite expected.py/notes.txt from the current baport

Besides matching, every ported file must compile and a second port must not change it.
After an intended change, run --update and review the diff of tests/cases in git.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import difflib
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "cases"
DEFAULT_ARGS = ["-t", "client"]


def baport(args: list[str], path: Path) -> str:
    """Ports path in place, returns what baport printed (paths relative to its directory)."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "baport.py"), *args, "-f", path.name],
        cwd=path.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        raise RuntimeError(f"baport exited with {result.returncode}:\n{result.stderr}")
    return result.stdout


def diff(expected: str, got: str, name: str, limit: int = 40) -> list[str]:
    lines = list(difflib.unified_diff(expected.splitlines(), got.splitlines(), f"{name} (expected)", f"{name} (got)", lineterm=""))
    return lines[:limit] + ([f"... {len(lines) - limit} more diff lines"] if len(lines) > limit else [])


def run_case(case: Path, update: bool) -> list[str]:
    """Returns the case's failures (empty when it passes)."""
    args_file = case / "args"
    args = shlex.split(args_file.read_text()) if args_file.exists() else DEFAULT_ARGS
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{case.name}.py"
        shutil.copyfile(case / "input.py", path)
        try:
            notes = baport(args, path)
            ported = path.read_text(encoding="utf-8")
            baport(args, path)
            again = path.read_text(encoding="utf-8")
        except RuntimeError as e:
            return [str(e)]
    failures = []
    try:
        compile(ported, path.name, "exec")
    except SyntaxError as e:
        failures.append(f"ported file doesn't compile: line {e.lineno}: {e.msg}")
    if again != ported:
        failures += ["porting twice changes the file:"] + diff(ported, again, "expected.py")
    if update:
        (case / "expected.py").write_text(ported, encoding="utf-8")
        (case / "notes.txt").write_text(notes, encoding="utf-8")
        return failures
    for name, got in (("expected.py", ported), ("notes.txt", notes)):
        expected_file = case / name
        if not expected_file.exists():
            failures.append(f"{name} is missing, run with --update")
        elif (expected := expected_file.read_text(encoding="utf-8")) != got:
            failures += diff(expected, got, name)
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="only run cases whose name contains one of these")
    parser.add_argument("--update", action="store_true", help="rewrite expected.py and notes.txt")
    options = parser.parse_args()
    cases = sorted(
        c for c in CASES.iterdir() if c.is_dir() and (not options.names or any(n in c.name for n in options.names))
    )
    if not cases:
        sys.exit("no cases match")
    with ThreadPoolExecutor() as pool:
        results = list(pool.map(lambda c: run_case(c, options.update), cases))
    failed = 0
    for case, failures in zip(cases, results):
        if failures:
            failed += 1
            print(f"FAIL {case.name}")
            print("\n".join("    " + line for line in failures))
    verb = "updated" if options.update else "passed"
    print(f"{len(cases) - failed} of {len(cases)} cases {verb}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
