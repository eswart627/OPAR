# scripts/ops/utils/run_teardown_sequence.py
# Purpose: Sequentially executes all teardown & cleanup commands defined in teardown_sequence.txt.
# Ensures strictly one-by-one execution with live status and timing. Supports --help and --list.

import os
import sys
import time
import argparse
import subprocess
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")

SEQUENCE_FILE = os.path.join(project_root, "teardown_sequence.txt")


def parse_commands(file_path: str):
    """Parses teardown_sequence.txt and extracts executable command lines with their descriptions."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Sequence file not found at: {file_path}")

    steps = []
    current_desc = ""

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                cleaned_comment = stripped.lstrip("#").strip()
                if not cleaned_comment.startswith("===") and not cleaned_comment.startswith("Run") and not cleaned_comment.startswith("Working"):
                    current_desc = cleaned_comment
                continue

            steps.append({
                "command": stripped,
                "description": current_desc or stripped,
            })
            current_desc = ""

    return steps


def list_steps(file_path: str):
    """Displays all teardown steps and commands without executing them."""
    try:
        steps = parse_commands(file_path)
    except Exception as e:
        print(f"[ERROR] Failed to read sequence file: {e}")
        sys.exit(1)

    print("=" * 85)
    print("  PERMISSION-AWARE RAG: TEARDOWN & RESET SEQUENCE (STEPS LIST)")
    print("=" * 85)
    print(f"Source: {file_path}\n")

    for idx, step in enumerate(steps, 1):
        print(f"  Step {idx}: {step['description']}")
        print(f"          Command: {step['command']}\n")

    print("=" * 85)
    print(f"Total: {len(steps)} sequential steps. Run without flags to execute.")
    print("=" * 85 + "\n")


def run_sequence():
    print("=" * 85)
    print("  PERMISSION-AWARE RAG: SEQUENTIAL TEARDOWN RUNNER")
    print("=" * 85)
    print(f"Reading commands from: {SEQUENCE_FILE}\n")

    try:
        steps = parse_commands(SEQUENCE_FILE)
    except Exception as e:
        print(f"[ERROR] Failed to read sequence file: {e}")
        sys.exit(1)

    print(f"Loaded {len(steps)} sequential steps to execute.\n")

    total_start = time.time()
    results = []

    for idx, step in enumerate(steps, 1):
        cmd = step["command"]
        desc = step["description"]

        print("-" * 85)
        print(f"  [STEP {idx}/{len(steps)}] {desc}")
        print(f"  Command: {cmd}")
        print("-" * 85)

        step_start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=project_root,
                check=False,
            )
            elapsed = round(time.time() - step_start, 2)
            if proc.returncode == 0:
                print(f"\n>>> [STEP {idx} SUCCESS] Completed in {elapsed}s\n")
                results.append((idx, desc, "PASS", elapsed))
            else:
                print(f"\n>>> [STEP {idx} FAILED] Exited with code {proc.returncode} in {elapsed}s\n")
                results.append((idx, desc, f"FAIL (exit {proc.returncode})", elapsed))
                print(f"[WARN] Step {idx} failed. Halting teardown sequence to prevent cascading issues.")
                break
        except Exception as e:
            elapsed = round(time.time() - step_start, 2)
            print(f"\n>>> [STEP {idx} ERROR] Execution failed: {e}\n")
            results.append((idx, desc, "ERROR", elapsed))
            break

    total_elapsed = round(time.time() - total_start, 2)

    print("=" * 85)
    print("  TEARDOWN SEQUENCE EXECUTION SUMMARY")
    print("=" * 85)
    for step_num, desc, status, dur in results:
        status_str = f"[{status}]"
        print(f"  Step {step_num}: {status_str:<12} {dur:>6.2f}s  -  {desc}")
    print("-" * 85)
    print(f"  Total Duration: {total_elapsed}s")
    print("=" * 85 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Sequential Multi-Cloud Teardown & Reset Runner for Permission-Aware RAG.",
        epilog="Examples:\n"
               "  python scripts/ops/utils/run_teardown_sequence.py --help\n"
               "  python scripts/ops/utils/run_teardown_sequence.py --list\n"
               "  python scripts/ops/utils/run_teardown_sequence.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all sequential teardown steps and commands without executing them.",
    )
    args = parser.parse_args()

    if args.list:
        list_steps(SEQUENCE_FILE)
    else:
        run_sequence()


if __name__ == "__main__":
    main()
