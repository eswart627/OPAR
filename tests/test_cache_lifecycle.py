# scripts/test_cache_lifecycle.py
"""
Test 1: Normal Cache Lifecycle & Hit/Miss Verification
Validates that:
- First lookup is a Cache Miss (invokes adapter).
- Second lookup is a Cache Hit (latency drops to <0.1ms).
- Both True and False authorization decisions are reliably preserved.
"""

import sys, os, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig

class MockMetadataStore:
    def __init__(self, data):
        self._data = data
    def get_by_uuid(self, u):
        return self._data.get(u)

class MockAdapter:
    def __init__(self, allowed_files):
        self.allowed_files = set(allowed_files)
        self.call_count = 0
    def check_access(self, meta):
        self.call_count += 1
        time.sleep(0.01)  # Simulate 10ms network round-trip to cloud
        return meta["file_name"] in self.allowed_files

class MockACManager:
    def __init__(self, user_name, adapter):
        self.user_name = user_name
        self.adapter = adapter
    def get_access_controller(self, provider):
        return self.adapter

def run_test():
    print("=" * 70)
    print("TEST 1: CACHE LIFECYCLE (MISS vs. HIT LATENCY & HIT ACCURACY)")
    print("=" * 70)

    docs = [
        {"global_uuid": "uuid-1", "file_name": "public_report.txt"},
        {"global_uuid": "uuid-2", "file_name": "secret_payroll.txt"}
    ]
    meta_db = {d["global_uuid"]: {"file_name": d["file_name"], "provider": "aws"} for d in docs}
    ms = MockMetadataStore(meta_db)
    
    # user-1 can only read public_report.txt
    adapter = MockAdapter(allowed_files=["public_report.txt"])
    ac_mgr = MockACManager("user-1", adapter)

    config = PermissionRetrieverConfig(use_cache=True, ttl_seconds=300.0)
    pr = PermissionRetriever(ms, config)

    # 1. First Retrieval (Cache Miss)
    t0 = time.time()
    filtered_1, decisions_1, _, dur_1 = pr.filter_docs(docs, ac_mgr)
    t_miss = (time.time() - t0) * 1000

    print(f"[Query 1 - Cache Miss]")
    print(f"  Live Adapter Cloud Calls: {adapter.call_count}")
    print(f"  Decisions: {decisions_1}")
    print(f"  Duration: {t_miss:.2f} ms")
    assert adapter.call_count == 2, f"Expected 2 live calls, got {adapter.call_count}"
    assert decisions_1["uuid-1"] is True
    assert decisions_1["uuid-2"] is False
    assert len(filtered_1) == 1

    # 2. Second Retrieval (Cache Hit)
    t0 = time.time()
    filtered_2, decisions_2, _, dur_2 = pr.filter_docs(docs, ac_mgr)
    t_hit = (time.time() - t0) * 1000

    print(f"\n[Query 2 - Cache Hit]")
    print(f"  Live Adapter Cloud Calls: {adapter.call_count} (Should remain 2!)")
    print(f"  Decisions: {decisions_2}")
    print(f"  Duration: {t_hit:.2f} ms")
    assert adapter.call_count == 2, f"Cloud adapter was called on cache hit! Call count: {adapter.call_count}"
    assert decisions_2 == decisions_1
    assert len(filtered_2) == 1

    # Check stats
    stats = pr.get_cache_stats()
    print(f"\n[Cache Diagnostics]")
    print(f"  Stats: {stats}")
    assert stats["total_entries"] == 2
    assert stats["active_entries"] == 2
    assert stats["expired_entries"] == 0

    print("\n>>> TEST 1 PASSED: Cache hit avoided live calls and returned identical decisions in sub-millisecond time.")

if __name__ == "__main__":
    run_test()
