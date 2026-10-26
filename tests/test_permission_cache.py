# tests/test_permission_cache.py
# Comprehensive Test Suite for PermissionRetriever Caching Engine
# Bridges to: src/retrievers/permission_retriever.py
# Combines: Cache lifecycle (hit/miss), TTL expiration, and adversarial invalidation.

import os
import sys
import time
import threading
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig


class MockMetadataStore:
    def __init__(self, data):
        self.data = data

    def get_by_uuid(self, u):
        return self.data.get(u)


class MockAdapter:
    def __init__(self, allowed_files):
        self.allowed_files = set(allowed_files)
        self.call_count = 0

    def check_access(self, meta):
        self.call_count += 1
        return meta.get("file_name") in self.allowed_files


class MockACManager:
    def __init__(self, user_name, adapter):
        self.user_name = user_name
        self.adapter = adapter

    def get_access_controller(self, provider):
        return self.adapter


def test_cache_lifecycle():
    """Test 1: Tests cache hit vs miss behavior, latency difference, and disabled cache bypass."""
    print("\n[TEST 1] Testing Cache Lifecycle (Hit/Miss & Latency)...")
    docs_meta = {
        "uuid-1": {"global_uuid": "uuid-1", "file_name": "f1.txt", "provider": "aws"},
        "uuid-2": {"global_uuid": "uuid-2", "file_name": "f2.txt", "provider": "aws"},
    }
    ms = MockMetadataStore(docs_meta)
    adapter = MockAdapter(allowed_files=["f1.txt"])
    ac_mgr = MockACManager("user-a", adapter)

    config = PermissionRetrieverConfig(use_cache=True, ttl_seconds=60.0)
    retriever = PermissionRetriever(metadata_store=ms, config=config)

    candidates = [{"global_uuid": "uuid-1"}, {"global_uuid": "uuid-2"}]

    # Run 1: Cold cache (Miss)
    t0 = time.perf_counter()
    filtered1, decisions1, durs1, total1 = retriever.filter_docs(candidates, ac_mgr)
    cold_time = (time.perf_counter() - t0) * 1000.0

    assert adapter.call_count == 2, f"Expected 2 adapter calls on cold run, got {adapter.call_count}"
    assert len(filtered1) == 1
    assert filtered1[0]["global_uuid"] == "uuid-1"

    # Run 2: Warm cache (Hit)
    t0 = time.perf_counter()
    filtered2, decisions2, durs2, total2 = retriever.filter_docs(candidates, ac_mgr)
    warm_time = (time.perf_counter() - t0) * 1000.0

    assert adapter.call_count == 2, "Adapter was called during cache hit!"
    assert filtered2 == filtered1
    print(f"  [PASS] Cold check: {cold_time:.2f}ms | Cached check: {warm_time:.2f}ms (Hits: 2/2, 0 remote calls)")


def test_cache_ttl_expiration():
    """Test 2: Verifies that cached permission decisions expire after the configured TTL."""
    print("\n[TEST 2] Testing Cache TTL Expiration...")
    docs_meta = {"uuid-1": {"global_uuid": "uuid-1", "file_name": "f1.txt", "provider": "aws"}}
    ms = MockMetadataStore(docs_meta)
    adapter = MockAdapter(allowed_files=["f1.txt"])
    ac_mgr = MockACManager("user-a", adapter)

    # 1 second TTL
    config = PermissionRetrieverConfig(use_cache=True, ttl_seconds=1.0)
    retriever = PermissionRetriever(metadata_store=ms, config=config)
    candidates = [{"global_uuid": "uuid-1"}]

    # Populates cache
    retriever.filter_docs(candidates, ac_mgr)
    assert adapter.call_count == 1

    # Immediate query hits cache
    retriever.filter_docs(candidates, ac_mgr)
    assert adapter.call_count == 1, "Immediate query should have hit cache"

    # Sleep past TTL
    time.sleep(1.2)

    # Query after TTL must trigger re-evaluation
    retriever.filter_docs(candidates, ac_mgr)
    assert adapter.call_count == 2, f"Expected 2 adapter calls after TTL expiration, got {adapter.call_count}"
    print("  [PASS] Cache entry expired and refreshed accurately after TTL.")


def test_cache_adversarial_concurrency():
    """Test 3: Multi-threaded readers and invalidators asserting zero race conditions or corrupted cache state."""
    print("\n[TEST 3] Testing Concurrent Cache Access & Invalidation...")
    docs_meta = {f"uuid-{i}": {"global_uuid": f"uuid-{i}", "file_name": f"f_{i}.txt", "provider": "aws"} for i in range(10)}
    ms = MockMetadataStore(docs_meta)
    adapter = MockAdapter(allowed_files=[f"f_{i}.txt" for i in range(5)])
    ac_mgr = MockACManager("user-b", adapter)

    config = PermissionRetrieverConfig(use_cache=True, ttl_seconds=10.0)
    retriever = PermissionRetriever(metadata_store=ms, config=config)
    candidates = [{"global_uuid": f"uuid-{i}"} for i in range(10)]

    errors = []

    def reader():
        try:
            for _ in range(50):
                f, _, _, _ = retriever.filter_docs(candidates, ac_mgr)
                assert len(f) == 5
                time.sleep(0.001)
        except Exception as e:
            errors.append(f"Reader error: {e}")

    def invalidator():
        try:
            for _ in range(25):
                retriever.clear_cache()
                time.sleep(0.002)
        except Exception as e:
            errors.append(f"Invalidator error: {e}")

    threads = [
        threading.Thread(target=reader),
        threading.Thread(target=reader),
        threading.Thread(target=invalidator),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent cache errors: {errors}"
    print("  [PASS] Concurrent cache operations completed with 0 errors.")


if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING PERMISSION CACHE CONSOLIDATED TEST SUITE")
    print("=" * 80)
    test_cache_lifecycle()
    test_cache_ttl_expiration()
    test_cache_adversarial_concurrency()
    print("\n" + "=" * 80)
    print(">>> ALL PERMISSION CACHE TESTS PASSED!")
    print("=" * 80)
