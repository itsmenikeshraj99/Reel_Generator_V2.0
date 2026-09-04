"""Run all 7 Phase 9 regression tests in sequence.

Usage:
    cd worker
    python -m tests.run_all_tests
    # or:
    ./venv/Scripts/python.exe tests/run_all_tests.py

Each test is in its own module. A failure in one test does not stop
the others — the runner collects pass/fail for all and exits with
non-zero if any failed (so CI can pick it up).
"""
import asyncio
import importlib
import os
import sys
import time
import traceback
from typing import List, Tuple

# Force UTF-8 stdout so emoji + box-drawing characters print on Windows
# (default cp1252 chokes on anything outside Latin-1).
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

# Make the tests/ directory importable so individual tests can do
# `from _helpers import …`.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from _helpers import C, section  # noqa: E402

# (module_name, display_name) — order matters for readability.
TESTS: List[Tuple[str, str]] = [
    ("test_01_corrupt_video", "Test 01: Corrupt video file"),
    ("test_02_oversize_video", "Test 02: Oversize video (>10 min)"),
    ("test_03_multi_subject", "Test 03: Multi-subject video"),
    ("test_04_malformed_gemini", "Test 04: Force-malformed Gemini response"),
    ("test_05_low_quality", "Test 05: Low-quality / invalid video"),
    ("test_06_mid_render_crash", "Test 06: Kill mid-render (resume)"),
    ("test_07_cross_user_rls", "Test 07: Cross-user RLS isolation"),
]


async def _run_one(name: str, display: str) -> bool:
    """Import and run a single test module's main()."""
    # NOTE: we do NOT call section() here — the test module's own
    # main() prints its own section header. Calling it here would
    # duplicate the header.
    t0 = time.time()
    try:
        mod = importlib.import_module(name)
        result = await mod.main()
        elapsed = time.time() - t0
        if result:
            print(f"\n  {C.OK}[OK] {display} passed in {elapsed:.1f}s{C.RESET}")
            return True
        else:
            print(f"\n  {C.FAIL}[FAIL] {display} FAILED in {elapsed:.1f}s{C.RESET}")
            return False
    except Exception as exc:  # noqa: BLE001
        elapsed = time.time() - t0
        print(f"\n  {C.FAIL}[CRASH] {display} CRASHED in {elapsed:.1f}s: {exc}{C.RESET}")
        traceback.print_exc()
        return False


async def main() -> int:
    print(f"{C.BOLD}Phase 9 — Regression Test Suite{C.RESET}")
    print(f"{C.BOLD}=============================={C.RESET}")
    print(f"Running {len(TESTS)} tests...\n")

    results: List[Tuple[str, bool, float]] = []
    for name, display in TESTS:
        t0 = time.time()
        passed = await _run_one(name, display)
        results.append((display, passed, time.time() - t0))
        print()  # blank line between tests

    # Summary
    section("Summary")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    for display, ok, elapsed in results:
        mark = f"{C.OK}PASS{C.RESET}" if ok else f"{C.FAIL}FAIL{C.RESET}"
        print(f"  [{mark}] {display} ({elapsed:.1f}s)")

    print()
    if failed == 0:
        print(f"{C.OK}{C.BOLD}All {passed} tests passed!{C.RESET}")
        return 0
    else:
        print(f"{C.FAIL}{C.BOLD}{failed} of {len(results)} tests failed.{C.RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
