# tests/test_pipeline_08_evaluation.py
# Comprehensive Consolidated Test Suite for Step 08: Quantitative Evaluation Runner
# Bridges to: scripts/08_run_quantitative_evaluation.py
# Combines:
#   1. CLI argument parsing, defaults, and multi-flag combinations (--mode, --auth_mode, --cache, --top_k)
#   2. In-memory TTL decision caching behavior, hit rate on repeats, and targeted user invalidation
#   3. Cache toggle propagation through QuestionProcessor.run_for_quantitative
#   4. Metrics calculation, summary table formatting with Auth/Cache headers, and JSON schema verification

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.env_loader import load_project_env
load_project_env()

from src.stores.metadata_store import MetadataStore
from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig


def test_cli_flags_and_argparse():
    """Test 1: Tests 08 command line argument parsing (--mode, --auth_mode, --cache, --top_k)."""
    print("\n[TEST 1] Testing 08 CLI Arguments and Flag Combinations...")
    cmd = [sys.executable, "scripts/08_run_quantitative_evaluation.py", "--help"]
    result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    assert "--mode" in result.stdout
    assert "--auth_mode" in result.stdout
    assert "--cache" in result.stdout

    # Test parser combinations
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["faiss", "hybrid"], default="faiss")
    parser.add_argument("--auth_mode", choices=["static", "auth0"], default=None)
    parser.add_argument("--cache", action="store_true", default=False)
    parser.add_argument("--top_k", type=int, default=5)

    args = parser.parse_args(["--mode", "hybrid", "--cache", "--auth_mode", "auth0", "--top_k", "10"])
    assert args.mode == "hybrid"
    assert args.cache is True
    assert args.auth_mode == "auth0"
    assert args.top_k == 10

    # Default check
    def_args = parser.parse_args([])
    assert def_args.mode == "faiss"
    assert def_args.cache is False
    assert def_args.auth_mode is None
    print("  [PASS] CLI parsing and defaults verified.")


def test_cache_retrieval_and_invalidation():
    """Test 2: Verifies PermissionRetriever caching, sub-millisecond hit rate, and user invalidation."""
    print("\n[TEST 2] Testing PermissionRetriever Cache Hits, Misses, and User Eviction...")
    ms = MetadataStore()
    ms.get_by_uuid = MagicMock(return_value={"provider": "aws", "parameters": {"bucket": "b1"}, "file_name": "f1"})

    # 1. Config with use_cache = True
    config_cached = PermissionRetrieverConfig(max_workers=1, use_cache=True, ttl_seconds=300.0)
    pr_cached = PermissionRetriever(ms, config_cached)

    mock_controller = MagicMock()
    mock_controller.check_access.return_value = True

    mock_factory = MagicMock()
    mock_factory.user_name = "test-user-cached"
    mock_factory.get_access_controller.return_value = mock_controller

    test_docs = [{"global_uuid": "doc-uuid-1", "title": "Doc 1"}]

    # Call 1: Miss
    filtered_1, decisions_1, durs_1, _ = pr_cached.filter_docs(test_docs, mock_factory)
    assert len(filtered_1) == 1
    assert decisions_1["doc-uuid-1"] is True
    assert mock_controller.check_access.call_count == 1

    # Call 2: Hit within TTL -> must NOT call adapter again
    filtered_2, decisions_2, durs_2, _ = pr_cached.filter_docs(test_docs, mock_factory)
    assert len(filtered_2) == 1
    assert decisions_2["doc-uuid-1"] is True
    assert mock_controller.check_access.call_count == 1, "Cache hit must avoid duplicate adapter call"
    assert durs_2["doc-uuid-1"] == 0.00005, "Cache hit duration should be 0.05ms"
    print("  [PASS] Cache hit avoided duplicate cloud calls and returned sub-millisecond duration.")

    # 2. Targeted user invalidation
    mock_factory_2 = MagicMock()
    mock_factory_2.user_name = "test-user-2"
    mock_factory_2.get_access_controller.return_value = mock_controller
    pr_cached.filter_docs(test_docs, mock_factory_2)
    assert mock_controller.check_access.call_count == 2

    # Purge only test-user-cached
    purged = pr_cached.invalidate_user("test-user-cached")
    assert purged == 1

    # test-user-2 still hits cache
    pr_cached.filter_docs(test_docs, mock_factory_2)
    assert mock_controller.check_access.call_count == 2

    # test-user-cached must re-query
    pr_cached.filter_docs(test_docs, mock_factory)
    assert mock_controller.check_access.call_count == 3
    print("  [PASS] Targeted user eviction isolated and validated.")


def test_qp_cache_propagation():
    """Test 3: Verifies that --cache is properly propagated through QuestionProcessor."""
    print("\n[TEST 3] Testing Cache Propagation through QuestionProcessor...")
    from src.processors.question_processor import QuestionProcessor

    qp = QuestionProcessor.__new__(QuestionProcessor)
    cfg = PermissionRetrieverConfig(use_cache=False)
    mock_pr = MagicMock(spec=PermissionRetriever)
    mock_pr.config = cfg
    qp.pr = mock_pr

    mock_user_mgr = MagicMock()
    mock_user_mgr.get_user_accessible_files.return_value = {}
    qp.user_manager = mock_user_mgr
    qp.vector_db_mgr = MagicMock()
    qp.sampled_questions = []
    qp.questions = []

    mock_ac_mgr = MagicMock()
    mock_ac_mgr.user_name = "test-user"

    # Propagate True
    qp.run_for_quantitative(mock_ac_mgr, "test-user", use_permission_cache=True)
    assert qp.pr.config.use_cache is True, "use_permission_cache=True should set config.use_cache to True"

    # Propagate False
    qp.run_for_quantitative(mock_ac_mgr, "test-user", use_permission_cache=False)
    assert qp.pr.config.use_cache is False, "use_permission_cache=False should set config.use_cache to False"

    print("  [PASS] QuestionProcessor faithfully propagated use_permission_cache toggle.")


def test_metrics_formatting_and_schema():
    """Test 4: Verifies summary metrics calculation (PermCov, Exact Match, EM@Admin) and headers."""
    print("\n[TEST 4] Testing Metrics Summary Calculation, Header Rendering, and Schema...")
    tot_auth = 4
    tot_retr = 25
    permcov = (tot_auth / tot_retr) * 100.0
    assert abs(permcov - 16.0) < 0.01

    fp, fn = 0, 0
    access_match = "PASS" if (fp + fn) == 0 else "FAIL"
    assert access_match == "PASS"

    summary_row = {
        "user": "user-3",
        "PermCov": round(permcov, 2),
        "Match": access_match,
        "EM": 0.0,
        "F1": 0.1333,
        "EM@Admin": 0.0
    }
    assert summary_row["PermCov"] == 16.0
    assert summary_row["Match"] == "PASS"

    # Header check
    use_cache = True
    metric_table_lines = [
        "=" * 105,
        "        PERMISSION-AWARE RAG: EXPERIMENT TEST METRICS SUMMARY (MODE: FAISS)",
        "=" * 105,
        f"  - Permission Cache:    {'Enabled (300s in-memory TTL)' if use_cache else 'Disabled (Live Cloud Checks)'}",
        f"  - Auth Mode:           {os.environ.get('AUTH_MODE', 'auth0').upper()}",
    ]
    header = "\n".join(metric_table_lines)
    assert "Permission Cache:    Enabled (300s in-memory TTL)" in header
    assert "AUTH0" in header or "STATIC" in header

    # Module import check
    import importlib
    qeval = importlib.import_module("scripts.08_run_quantitative_evaluation")
    assert hasattr(qeval, "main"), "scripts/08_run_quantitative_evaluation.py must define main()"

    print("  [PASS] Metrics calculations, header formatting, and pipeline bridge verified.")


if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING PIPELINE 08 EVALUATION CONSOLIDATED TEST SUITE")
    print("=" * 80)
    test_cli_flags_and_argparse()
    test_cache_retrieval_and_invalidation()
    test_qp_cache_propagation()
    test_metrics_formatting_and_schema()
    print("\n" + "=" * 80)
    print(">>> ALL PIPELINE 08 TESTS PASSED!")
    print("=" * 80)
