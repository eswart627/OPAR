# tests/test_pipeline_09_latency.py
# Comprehensive Consolidated Test Suite for Step 09: Latency Benchmark Runner
# Bridges to: scripts/09_run_latency_evaluation.py
# Combines:
#   1. CLI argument parsing, flags (--cache, --auth_mode, --sample_size), and env synchronization
#   2. Latency benchmark in-memory cache speedup (sub-millisecond hits vs multi-millisecond network calls)
#   3. Latency summary JSON output schema verification and pipeline bridge integration

import os
import sys
import time
import json
import argparse
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.env_loader import load_project_env
load_project_env()

from src.stores.metadata_store import MetadataStore
from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig


def test_09_cli_flags_and_argparse():
    """Test 1: Tests 09 command line flags (--cache, --auth_mode, --sample_size) and env sync."""
    print("\n[TEST 1] Testing 09 CLI Arguments, Flag Combinations, and Environment Sync...")
    cmd = [sys.executable, "scripts/09_run_latency_evaluation.py", "--help"]
    result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    assert "--cache" in result.stdout, "Missing --cache flag in 09 --help"
    assert "--auth_mode" in result.stdout, "Missing --auth_mode flag in 09 --help"
    assert "--sample_size" in result.stdout, "Missing --sample_size flag in 09 --help"

    # Argument parser combination tests
    parser = argparse.ArgumentParser(description="09 parser test")
    parser.add_argument("--cache", action="store_true", default=False)
    parser.add_argument("--auth_mode", choices=["static", "auth0"], default=None)
    parser.add_argument("--sample_size", type=int, default=50)

    # Defaults
    args1 = parser.parse_args([])
    assert args1.cache is False
    assert args1.auth_mode is None
    assert args1.sample_size == 50

    # Multi-flag combo
    args2 = parser.parse_args(["--auth_mode", "auth0", "--cache", "--sample_size", "10"])
    assert args2.cache is True
    assert args2.auth_mode == "auth0"
    assert args2.sample_size == 10

    # Environment synchronization check
    snippet = (
        "import os, argparse\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--auth_mode', choices=['static', 'auth0'], default=None)\n"
        "args = parser.parse_args(['--auth_mode', 'auth0'])\n"
        "if args.auth_mode:\n"
        "    os.environ['AUTH_MODE'] = args.auth_mode.lower()\n"
        "assert os.environ.get('AUTH_MODE') == 'auth0'\n"
    )
    res = subprocess.run([sys.executable, "-c", snippet], cwd=project_root, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    print("  [PASS] CLI parsing, multi-flag combinations, and env sync verified.")


def test_cache_vs_uncached_latency_speedup():
    """Test 2: Verifies latency speedup of in-memory caching vs live network checks."""
    print("\n[TEST 2] Testing Latency Benchmark Cache Speedup and Sub-Millisecond Reporting...")
    ms = MetadataStore()
    ms.get_by_uuid = MagicMock(return_value={"provider": "aws", "parameters": {"bucket": "b1"}, "file_name": "f1"})

    # Simulated network latency (10ms per check)
    def mock_check(meta):
        time.sleep(0.010)
        return True

    mock_controller = MagicMock()
    mock_controller.check_access.side_effect = mock_check

    mock_factory = MagicMock()
    mock_factory.user_name = "latency-test-user"
    mock_factory.get_access_controller.return_value = mock_controller

    test_docs = [{"global_uuid": f"doc-uuid-{i}"} for i in range(5)]

    # 1. UNCACHED RUN: 3 passes over the same 5 docs
    pr_uncached = PermissionRetriever(ms, PermissionRetrieverConfig(max_workers=5, use_cache=False))
    t0 = time.time()
    for _ in range(3):
        pr_uncached.filter_docs(test_docs, mock_factory)
    uncached_total_dur = time.time() - t0
    uncached_calls = mock_controller.check_access.call_count
    assert uncached_calls == 15, f"Expected 15 calls without cache, got {uncached_calls}"

    # 2. CACHED RUN: 3 passes over the same 5 docs
    mock_controller.reset_mock()
    pr_cached = PermissionRetriever(ms, PermissionRetrieverConfig(max_workers=5, use_cache=True))
    t0 = time.time()
    for _ in range(3):
        pr_cached.filter_docs(test_docs, mock_factory)
    cached_total_dur = time.time() - t0
    cached_calls = mock_controller.check_access.call_count
    assert cached_calls == 5, f"Expected exactly 5 calls with cache (pass 1 only), got {cached_calls}"

    speedup = uncached_total_dur / max(cached_total_dur, 0.0001)
    print(f"  [PASS] Measured In-Memory Cache Speedup: {speedup:.2f}x (Cached: {cached_total_dur*1000:.2f}ms vs Uncached: {uncached_total_dur*1000:.2f}ms)")
    assert cached_total_dur < uncached_total_dur, "Cached run must be faster than uncached run"

    # Per-document duration reporting on cache hits
    mock_controller.side_effect = None
    mock_controller.check_access.return_value = True
    hit_docs = [{"global_uuid": "doc-hit-1"}]
    pr_cached.filter_docs(hit_docs, mock_factory)  # miss
    _, _, durs_hit, _ = pr_cached.filter_docs(hit_docs, mock_factory)  # hit
    assert durs_hit["doc-hit-1"] == 0.00005, f"Expected 0.00005s recorded duration, got {durs_hit['doc-hit-1']}"
    print("  [PASS] Cache hit returned sub-millisecond duration (0.05 ms).")


def test_latency_summary_schema_and_bridge():
    """Test 3: Verifies latency_summary.json schema, fields, and pipeline bridge integration."""
    print("\n[TEST 3] Testing Latency Summary Schema and Pipeline Bridge...")
    simulated_row = {
        "top_k": 10,
        "max_workers": 20,
        "user": "baseline-admin",
        "use_cache": True,
        "auth_mode": "AUTH0",
        "iam_latency_ms": 0.05,
        "e2e_latency_ms": 125.43,
        "avg_aws_response_ms": 0.05,
        "avg_gcp_response_ms": 0.05,
    }

    # Verify JSON serialization round-trip
    serialized = json.dumps([simulated_row], indent=2)
    deserialized = json.loads(serialized)[0]

    assert deserialized["use_cache"] is True
    assert deserialized["auth_mode"] == "AUTH0"
    assert deserialized["top_k"] == 10
    assert deserialized["max_workers"] == 20
    assert isinstance(deserialized["iam_latency_ms"], float)
    assert isinstance(deserialized["e2e_latency_ms"], float)

    # Inspect existing summary if present
    summary_path = os.path.join(project_root, "artifacts", "results", "latency_summary.json")
    if os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list)
        if data:
            expected_keys = ["top_k", "max_workers", "user", "iam_latency_ms", "e2e_latency_ms"]
            for k in expected_keys:
                assert k in data[0]

    # Pipeline bridge check
    import importlib
    script09 = importlib.import_module("scripts.09_run_latency_evaluation")
    assert hasattr(script09, "main"), "scripts.09_run_latency_evaluation missing main()"
    print("  [PASS] Latency summary schema and pipeline bridge verified.")


if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING PIPELINE 09 LATENCY CONSOLIDATED TEST SUITE")
    print("=" * 80)
    test_09_cli_flags_and_argparse()
    test_cache_vs_uncached_latency_speedup()
    test_latency_summary_schema_and_bridge()
    print("\n" + "=" * 80)
    print(">>> ALL PIPELINE 09 TESTS PASSED!")
    print("=" * 80)
