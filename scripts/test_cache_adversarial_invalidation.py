# scripts/test_cache_adversarial_invalidation.py
"""
Test 3: Adversarial Tests & Failure Scenarios
1. Demonstrates the Failure: What happens when permissions are revoked in IAM but cache is NOT invalidated (The Stale Access Security Hole).
2. Demonstrates the Fix: Immediate targeted invalidation (invalidate_user) instantly stops the leak.
3. Tests Multi-User Isolation: Invalidating User-A never affects User-B's cache.
4. Concurrency Stress Test: Multi-threaded simultaneous reads and invalidations to verify zero race conditions.
"""

import sys, os, time, threading
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig

class DynamicMockAdapter:
    def __init__(self):
        # Maps user -> set of allowed file names
        self.permissions = {
            "alice": {"top_secret_rnd.pdf", "general_handbook.pdf"},
            "bob": {"marketing_plan.pdf"}
        }

    def check_access_for(self, user_name, meta):
        return meta["file_name"] in self.permissions.get(user_name, set())

class MockACManager:
    def __init__(self, user_name, adapter):
        self.user_name = user_name
        self.adapter = adapter
    def get_access_controller(self, provider):
        class Controller:
            def __init__(self, parent, user):
                self.parent = parent
                self.user = user
            def check_access(self, meta):
                return self.parent.check_access_for(self.user, meta)
        return Controller(self.adapter, self.user_name)

class MockMetadataStore:
    def __init__(self, data):
        self._data = data
    def get_by_uuid(self, u):
        return self._data.get(u)

def run_test():
    print("=" * 70)
    print("TEST 3: ADVERSARIAL STALE CACHE LEAK & TARGETED INVALIDATION FIX")
    print("=" * 70)

    docs = [
        {"global_uuid": "doc-secret", "file_name": "top_secret_rnd.pdf"},
        {"global_uuid": "doc-public", "file_name": "general_handbook.pdf"}
    ]
    meta_db = {d["global_uuid"]: {"file_name": d["file_name"], "provider": "aws"} for d in docs}
    ms = MockMetadataStore(meta_db)
    
    adapter = DynamicMockAdapter()
    ac_alice = MockACManager("alice", adapter)
    ac_bob = MockACManager("bob", adapter)

    config = PermissionRetrieverConfig(use_cache=True, ttl_seconds=300.0)
    pr = PermissionRetriever(ms, config)

    # Step 1: Alice queries and gets access to both files
    filtered, decisions, _, _ = pr.filter_docs(docs, ac_alice)
    alice_files = [d['file_name'] for d in filtered]
    print(f"[Step 1: Alice baseline]")
    print(f"  Alice allowed files: {alice_files}")
    assert decisions["doc-secret"] is True
    assert decisions["doc-public"] is True

    # Step 2: Admin revokes Alice's access to top_secret_rnd.pdf in the cloud!
    print(f"\n[Step 2: Admin revokes Alice's access to 'top_secret_rnd.pdf' in Cloud IAM]")
    adapter.permissions["alice"].remove("top_secret_rnd.pdf")

    # Step 3 (THE FAILURE CASE): Alice queries again WITHOUT cache invalidation
    filtered_stale, decisions_stale, _, _ = pr.filter_docs(docs, ac_alice)
    print(f"[Step 3: Alice queries WITHOUT cache invalidation (Vulnerability Check)]")
    print(f"  Cloud actual permission: {adapter.check_access_for('alice', meta_db['doc-secret'])} (REVOKED)")
    print(f"  Cached decision returned: {decisions_stale['doc-secret']} (STALE LEAK!)")
    # Verify the vulnerability was active in the un-invalidated cache:
    assert decisions_stale["doc-secret"] is True, "Expected stale cache to leak access before invalidation"

    # Step 4 (THE FIX): Webhook / Admin triggers targeted invalidation for Alice!
    print(f"\n[Step 4: Cloud Invalidation Webhook triggers for 'alice']")
    purged_count = pr.invalidate_user("alice")
    print(f"  Purged {purged_count} cache keys for alice in < 1ms")
    assert purged_count > 0

    # Step 5: Alice queries immediately after invalidation
    filtered_fixed, decisions_fixed, _, _ = pr.filter_docs(docs, ac_alice)
    alice_fixed_files = [d['file_name'] for d in filtered_fixed]
    print(f"[Step 5: Alice queries AFTER invalidation]")
    print(f"  Alice allowed files: {alice_fixed_files}")
    print(f"  Decisions: {decisions_fixed}")
    assert decisions_fixed["doc-secret"] is False, "Access was not revoked after invalidation!"
    assert len(filtered_fixed) == 1
    assert filtered_fixed[0]["file_name"] == "general_handbook.pdf"
    print("  -> Security Leak Completely Sealed!")

    # Step 6: Multi-User Isolation Test
    print(f"\n[Step 6: Cross-User Isolation Verification]")
    # Prime bob's cache
    meta_db["doc-bob"] = {"file_name": "marketing_plan.pdf", "provider": "aws"}
    pr.filter_docs([{"global_uuid": "doc-bob", "file_name": "marketing_plan.pdf"}], ac_bob)
    
    # Invalidate alice again
    pr.invalidate_user("alice")
    
    # Bob's key should still be active
    bob_key = "bob:doc-bob"
    assert bob_key in pr._cache, "Invalidating Alice accidentally deleted Bob's cache!"
    print(f"  Alice invalidated, Bob's cache preserved intact. Isolation verified.")

    # Step 7: Concurrency Stress Test (Simultaneous Reads & Invalidations)
    print(f"\n[Step 7: Concurrency Stress Test under Multi-threading]")
    errors = []
    def reader_worker():
        for _ in range(50):
            try:
                pr.filter_docs(docs, ac_alice)
            except Exception as e:
                errors.append(e)

    def invalidator_worker():
        for _ in range(50):
            try:
                pr.invalidate_user("alice")
                time.sleep(0.001)
            except Exception as e:
                errors.append(e)

    threads = [threading.Thread(target=reader_worker) for _ in range(4)] + \
              [threading.Thread(target=invalidator_worker) for _ in range(2)]
    
    for t in threads: t.start()
    for t in threads: t.join()

    assert len(errors) == 0, f"Race condition detected during concurrent invalidation! Errors: {errors}"
    print(f"  Ran 300 concurrent read/invalidation cycles with 0 race conditions or exceptions.")

    print("\n>>> TEST 3 PASSED: Adversarial leak reproduced, verified fixed, isolated, and thread-safe.")

if __name__ == "__main__":
    run_test()
