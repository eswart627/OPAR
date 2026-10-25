# scripts/test_cache_ttl_expiration.py
"""
Test 2: Unix Timestamp TTL Auto-Eviction
Validates that:
- Entries are valid before TTL expiration.
- Once (now > expires_at), entries are automatically dropped on next query.
- A fresh cloud check is triggered, preventing indefinite caching.
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
    def __init__(self):
        self.call_count = 0
    def check_access(self, meta):
        self.call_count += 1
        return True

class MockACManager:
    def __init__(self, user_name, adapter):
        self.user_name = user_name
        self.adapter = adapter
    def get_access_controller(self, provider):
        return self.adapter

def run_test():
    print("=" * 70)
    print("TEST 2: UNIX TIMESTAMP TTL EXPIRATION & AUTO-EVICTION")
    print("=" * 70)

    docs = [{"global_uuid": "doc-alpha", "file_name": "quarterly_budget.pdf"}]
    ms = MockMetadataStore({"doc-alpha": {"file_name": "quarterly_budget.pdf", "provider": "gcp"}})
    adapter = MockAdapter()
    ac_mgr = MockACManager("user-audit", adapter)

    # Configure a fast 1.0 second TTL for testing
    TEST_TTL = 1.0
    config = PermissionRetrieverConfig(use_cache=True, ttl_seconds=TEST_TTL)
    pr = PermissionRetriever(ms, config)

    # 1. Query at t=0 (Fresh cache miss)
    pr.filter_docs(docs, ac_mgr)
    print(f"t = 0.0s | Initial query executed. Cloud calls: {adapter.call_count}")
    assert adapter.call_count == 1

    # 2. Query at t=0.4s (Within 1.0s TTL)
    time.sleep(0.4)
    pr.filter_docs(docs, ac_mgr)
    print(f"t = 0.4s | Query within TTL executed. Cloud calls: {adapter.call_count} (Hit!)")
    assert adapter.call_count == 1, "Cache failed to hit within valid TTL window!"

    # 3. Sleep past TTL (1.0s) to t=1.3s
    print(f"Waiting 0.9s to cross 1.0s TTL boundary...")
    time.sleep(0.9)
    # Total elapsed ~ 1.3s > 1.0s TTL
    
    # 4. Query after expiration
    pr.filter_docs(docs, ac_mgr)
    print(f"t = 1.3s | Query after TTL expiration executed. Cloud calls: {adapter.call_count} (Fresh check forced!)")
    assert adapter.call_count == 2, f"Expected cache eviction to force call #2, but call count was {adapter.call_count}"

    print("\n>>> TEST 2 PASSED: Expired entries automatically invalidated and forced fresh cloud check.")

if __name__ == "__main__":
    run_test()
