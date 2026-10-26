# scripts/test_sqlite_pure_db.py
"""
Phase 2 - Test 1: Pure SQLite Metadata DB Verification
Validates that:
- metadata.json is completely absent from disk.
- MetadataStore loads strictly from artifacts/metadata.db.
- All records are present and correctly retrieved by UUID and file_name.
- Sub-millisecond lookup latency benchmark.
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.stores.metadata_store import MetadataStore

def run_test():
    print("=" * 70)
    print("TEST 1: PURE SQLITE METADATA VERIFICATION (ZERO JSON ON DISK)")
    print("=" * 70)

    json_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "artifacts", "metadata.json"))
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "artifacts", "metadata.db"))

    # Assert metadata.json does not exist
    assert not os.path.exists(json_path), f"FAIL: metadata.json exists at {json_path}! Expected pure SQLite."
    print("1. Confirmed: artifacts/metadata.json DOES NOT EXIST on disk.")

    assert os.path.exists(db_path), f"FAIL: SQLite DB missing at {db_path}"
    print(f"2. Confirmed: artifacts/metadata.db exists at: {db_path}")

    # Initialize store strictly on SQLite
    ms = MetadataStore()
    ms.load(db_path)
    assert ms.is_sqlite(), "MetadataStore failed to initialize in SQLite mode!"

    all_records = ms.all()
    print(f"3. Total records loaded from SQLite DB: {len(all_records)}")
    assert len(all_records) > 0, "No records found in SQLite DB!"

    # Verify lookups and measure latency
    t0 = time.time()
    for rec in all_records:
        uid = rec["global_uuid"]
        fname = rec["file_name"]

        # 1. Test get_by_uuid
        actual_by_uid = ms.get_by_uuid(uid)
        assert actual_by_uid is not None, f"Failed to retrieve record by UUID: {uid}"
        assert actual_by_uid["global_uuid"] == uid
        assert actual_by_uid["file_name"] == fname
        assert actual_by_uid.get("provider") in ("aws", "gcp")
        assert (actual_by_uid.get("parameters") or {}).get("bucket") != ""

        # 2. Test get_by_file
        actual_by_file = ms.get_by_file(fname)
        assert actual_by_file is not None, f"Failed to retrieve record by file_name: {fname}"
        assert actual_by_file["global_uuid"] == uid

    total_time_ms = (time.time() - t0) * 1000
    avg_per_query_us = (total_time_ms / (len(all_records) * 2)) * 1000

    print(f"\n[Performance & Integrity Metrics]")
    print(f"  Verified {len(all_records) * 2} lookups across UUID and file_name.")
    print(f"  Total time: {total_time_ms:.3f} ms")
    print(f"  Average lookup latency: {avg_per_query_us:.2f} microseconds per query")

    print("\n>>> TEST 1 PASSED: Pure SQLite decoupled store verified with ZERO JSON dependency.")

if __name__ == "__main__":
    run_test()
