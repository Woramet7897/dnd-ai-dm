"""
run_tests.py — Central Test Runner for DnD AI DM
Usage:
    python run_tests.py              # Runs all test suites in tests/
    python run_tests.py 13.5         # Runs tests for a specific phase (e.g. Phase 13.5)
    python run_tests.py phase8       # Runs tests matching 'phase8'
    python run_tests.py -v           # Verbose mode (shows full stdout/stderr of each suite)
"""

import os
import sys
import time
import argparse
import subprocess
from typing import List, Tuple

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(PROJECT_ROOT, "tests")

# Logical execution order
ORDERED_TEST_FILES = [
    "phase3_tests.py",
    "phase4_tests.py",
    "phase5_tests.py",
    "phase6_tests.py",
    "phase7_tests.py",
    "phase8_tests.py",
    "phase9_tests.py",
    "phase10_tests.py",
    "phase11_1_tests.py",
    "phase11_2_tests.py",
    "phase11_3_tests.py",
    "phase12_1_tests.py",
    "phase12_2_tests.py",
    "phase12_3_tests.py",
    "phase13_tests.py",
    "phase13_5_tests.py",
    "phase14_1_tests.py",
    "phase14_2_tests.py",
    "phase_equipment_tests.py",
    "test_app_integrations.py",
    "phase15_gemini_tests.py",
    "test_dynamic_npcs.py",
    "test_dynamic_events.py",
    "test_dynamic_spells.py",
]


def find_test_files(target_phases: List[str]) -> List[str]:
    """Find and filter test files matching given targets."""
    all_files = []
    if os.path.isdir(TESTS_DIR):
        for f in os.listdir(TESTS_DIR):
            if f.endswith("_tests.py") or f.startswith("test_") and f.endswith(".py"):
                if f not in all_files:
                    all_files.append(f)

    # Sort based on logical order
    ordered = [f for f in ORDERED_TEST_FILES if f in all_files]
    for f in all_files:
        if f not in ordered:
            ordered.append(f)

    if not target_phases:
        return ordered

    selected = []
    for f in ordered:
        name_lower = f.lower()
        for t in target_phases:
            t_norm = t.lower().replace(".", "_").replace("-", "_")
            if t_norm in name_lower or t.lower() in name_lower:
                if f not in selected:
                    selected.append(f)
                break
    return selected


def run_single_suite(test_file: str, verbose: bool = False) -> Tuple[bool, float, str]:
    """Run an isolated test suite in a subprocess with Python -X utf8."""
    test_path = os.path.join(TESTS_DIR, test_file)
    cmd = [sys.executable, "-X", "utf8", "-m", "unittest", test_file]
    env = os.environ.copy()
    env["PYTHONPATH"] = PROJECT_ROOT

    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=TESTS_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    elapsed = time.time() - t0

    # If unittest discover didn't find standard TestCase (e.g. phase3, 4, 5 custom runner), run file directly
    if proc.returncode != 0 and "Ran 0 tests" in proc.stderr:
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", test_file],
            cwd=TESTS_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        elapsed = time.time() - t0

    success = (proc.returncode == 0)
    output = proc.stdout + "\n" + proc.stderr
    return success, elapsed, output


def extract_test_count(output: str) -> int:
    """Extract individual test or assertion count from suite output."""
    import re
    m = re.search(r"Ran (\d+) tests?", output)
    if m:
        return int(m.group(1))
    m = re.search(r"RESULTS:\s+(\d+)\s+passed", output)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s+checks? passed", output)
    if m:
        return int(m.group(1))
    return 0


def main():
    parser = argparse.ArgumentParser(description="DnD AI DM Central Test Runner")
    parser.add_argument("phases", nargs="*", help="Optional phase numbers or names to test (e.g. 13.5, phase8)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output of test logs")
    args = parser.parse_args()

    test_files = find_test_files(args.phases)
    if not test_files:
        print(f"❌ No test files found matching criteria: {args.phases}")
        sys.exit(1)

    print("=" * 70)
    print(f"🧪 Running {len(test_files)} Test Suite(s)...")
    print("=" * 70)

    results = []
    total_start = time.time()

    for idx, f in enumerate(test_files, 1):
        print(f"[{idx:02d}/{len(test_files):02d}] {f:<30} ... ", end="", flush=True)

        passed, duration, out = run_single_suite(f, verbose=args.verbose)
        test_cnt = extract_test_count(out)
        status_str = f"PASS ✅ ({test_cnt:>3} tests)" if passed else "FAIL ❌"
        print(f"{status_str} ({duration:.2f}s)")

        if args.verbose or not passed:
            for line in out.strip().splitlines()[-10:]:
                print(f"    {line}")
            if not passed:
                print("    " + "-" * 60)

        results.append((f, passed, duration, test_cnt))

    total_time = time.time() - total_start
    all_passed = all(p for _, p, _, _ in results)
    pass_count = sum(1 for _, p, _, _ in results if p)
    fail_count = len(results) - pass_count
    total_tests = sum(c for _, _, _, c in results)

    print("=" * 70)
    print("📊 EXACT PASS COUNT PER PHASE FILE:")
    print("-" * 70)
    for f, passed, duration, count in results:
        status_icon = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status_icon} | {f:<28} | {count:>3} tests | {duration:.2f}s")
    print("-" * 70)
    print(f"Total Individual Tests / Checks Passed: {total_tests}")
    print("=" * 70)
    if all_passed:
        print(f"🎉 ALL TEST SUITES PASSED! ({pass_count}/{len(results)} suites, {total_tests} tests in {total_time:.2f}s)")
    else:
        print(f"❌ TEST FAILURES DETECTED: {fail_count} failed, {pass_count} passed in {total_time:.2f}s")
    print("=" * 70)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
