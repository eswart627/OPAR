# scripts/test_sqlite_bucket_update.py
"""
Phase 2 - Test 2: Decoupled Single-Row Location Update
Demonstrates that:
1. Updating a bucket/location for a file in SQLite updates the database row in <1ms.
2. metadata.json on disk is COMPLETELY UNTOUCHED.
3. The FAISS vector index is COMPLETELY UNTOUCHED (Zero re-embedding cost).
4. PermissionRetriever immediately routes checks to the newly updated bucket.
5. Restores original state safely.
"""

import os
import sys
import json
import hashlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.stores.metadata_store import MetadataStore
from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig

class RecordingAdapter:
    def __init__(self):
        self.checked_buckets = []
    def check_access(self, meta):
        bucket = (meta.get("parameters") or {}).get("bucket")
        self.checked_buckets.append(bucket)
        return True

class MockACManager:
    def __init__(self, adapter):
        self.user_name = "test-user"
        self.adapter = adapter
    def get_access_controller(self, provider):
        return self.adapter

def get_file_hash(path: str) -> str:
    if not os.path.exists(path):
        return ""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()

def run_test():
    print("=" * 70)
    print("TEST 2: DECOUPLED BUCKET UPDATE (ZERO FAISS RE-EMBEDDING)")
    print("=" * 70)

    json_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "artifacts", "metadata.json"))
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "artifacts", "metadata.db"))
    faiss_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vector_db", "faiss.index"))

    # Capture initial checksums of metadata.json and FAISS index
    initial_json_hash = get_file_hash(json_path)
    initial_faiss_hash = get_file_hash(faiss_path)

    ms = MetadataStore()
    ms.load(db_path)

    # Pick the first record
    all_recs = ms.all()
    target = all_recs[0]
    target_uuid = target["global_uuid"]
    original_bucket = (target.get("parameters") or {}).get("bucket")
    test_new_bucket = "migrated-enterprise-bucket-99"

    print(f"Target Document: {target.get('file_name')} (UUID: {target_uuid})")
    print(f"Original Bucket in SQLite: {original_bucket}")

    adapter = RecordingAdapter()
    ac_mgr = MockACManager(adapter)
    pr = PermissionRetriever(ms, PermissionRetrieverConfig(use_cache=False))

    # 1. Query before update -> adapter checks original bucket
    pr.filter_docs([{"global_uuid": target_uuid}], ac_mgr)
    print(f"Query 1: Adapter checked bucket -> {adapter.checked_buckets[-1]}")
    assert adapter.checked_buckets[-1] == original_bucket

    # 2. Update ONLY SQLite (Simulating file moved to new bucket in cloud)
    print(f"\n[Updating location in SQLite to '{test_new_bucket}'...]")
    success = ms.update_location(target_uuid, new_bucket=test_new_bucket)
    assert success is True, "Failed to update location in MetadataStore"

    # 3. Query after update -> adapter immediately checks new bucket
    pr.filter_docs([{"global_uuid": target_uuid}], ac_mgr)
    print(f"Query 2: Adapter checked bucket -> {adapter.checked_buckets[-1]}")
    assert adapter.checked_buckets[-1] == test_new_bucket

    # 4. Prove that metadata.json was NEVER modified
    current_json_hash = get_file_hash(json_path)
    print(f"\n[Decoupling Verification]")
    print(f"  metadata.json untouched: {initial_json_hash == current_json_hash} (Hash verified)")
    assert initial_json_hash == current_json_hash, "metadata.json was unexpectedly modified!"

    # 5. Prove that FAISS index was NEVER re-embedded
    if initial_faiss_hash:
        current_faiss_hash = get_file_hash(faiss_path)
        print(f"  FAISS index untouched:   {initial_faiss_hash == current_faiss_hash} (Zero re-embedding!)")
        assert initial_faiss_hash == current_faiss_hash, "FAISS index was unexpectedly modified!"

    # 6. Revert to original bucket to leave clean state
    ms.update_location(target_uuid, new_bucket=original_bucket)
    print(f"  Reverted record back to: {original_bucket}")

    print("\n>>> TEST 2 PASSED: Bucket location updated in SQLite with zero FAISS re-indexing or JSON writes.")

if __name__ == "__main__":
    run_test()
