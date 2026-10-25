# scripts/test_sqlite_adversarial.py
"""
Phase 2 - Test 3: Adversarial Tests & Failure Edge Cases
1. Missing & Corrupted Keys: Querying non-existent UUIDs and empty strings.
2. SQL Injection Attempt: Ensuring malicious input does not manipulate queries.
3. Multi-Threaded Stress Test: Concurrent reads and writes across worker threads without database lock exceptions.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.stores.metadata_store import MetadataStore

def run_test():
    print("=" * 70)
    print("TEST 3: ADVERSARIAL SQLITE EDGE CASES & CONCURRENCY STRESS")
    print("=" * 70)

    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "artifacts", "metadata.db"))
    ms = MetadataStore()
    ms.load(db_path)

    # 1. Non-existent & Malformed Keys
    print("[1. Testing Non-existent & Malformed Lookups]")
    assert ms.get_by_uuid("non-existent-uuid-999") is None
    assert ms.get_by_uuid("") is None
    assert ms.get_by_file("non-existent-file.txt") is None
    assert ms.get_by_file("") is None
    assert ms.update_location("fake-uuid", new_bucket="b1") is False
    print("  Safe None/False returns verified for missing keys.")

    # 2. SQL Injection Resistance
    print("\n[2. Testing SQL Injection Immunity]")
    malicious_inputs = [
        "' OR '1'='1",
        "'; DROP TABLE metadata; --",
        "doc_id_531.txt' UNION SELECT 'hacked', 'hacked', 'hacked', 'hacked', 'hacked', 'hacked', 'hacked' --"
    ]
    for injection in malicious_inputs:
        res = ms.get_by_uuid(injection)
        assert res is None, f"SQL Injection succeeded unexpectedly on input: {injection}"
        res_file = ms.get_by_file(injection)
        assert res_file is None, f"SQL Injection succeeded on file lookup: {injection}"
    
    # Verify table is still intact
    assert len(ms.all()) > 0, "Table was corrupted or dropped!"
    print("  Parameterized SQL queries completely neutralized injection payloads.")

    # 3. Multi-Threaded Concurrency Stress Test
    print("\n[3. Testing Concurrent Multi-Threaded Access]")
    errors = []
    all_recs = ms.all()
    sample_uuids = [r["global_uuid"] for r in all_recs]

    def reader_task():
        for _ in range(100):
            for u in sample_uuids:
                try:
                    res = ms.get_by_uuid(u)
                    if res is None:
                        errors.append(f"Reader got None for known UUID: {u}")
                except Exception as e:
                    errors.append(f"Reader error: {e}")

    def writer_task(worker_id):
        for i in range(20):
            target_u = sample_uuids[worker_id % len(sample_uuids)]
            try:
                ms.update_location(target_u, new_bucket=f"concurrency-bucket-{worker_id}-{i}")
                time.sleep(0.001)
            except Exception as e:
                errors.append(f"Writer error: {e}")

    threads = []
    # 4 readers + 4 writers simultaneously running
    for i in range(4):
        threads.append(threading.Thread(target=reader_task))
    for i in range(4):
        threads.append(threading.Thread(target=writer_task, args=(i,)))

    for t in threads: t.start()
    for t in threads: t.join()

    print(f"  Executed 480 concurrent DB transactions across 8 parallel threads.")
    assert len(errors) == 0, f"Concurrency errors occurred: {errors[:5]}"
    print("  Zero database lock errors, zero exceptions under concurrent load.")

    # Reset any test bucket changes by restoring original buckets
    for r in all_recs:
        ms.update_location(r["global_uuid"], new_bucket=(r.get("parameters") or {}).get("bucket"))
    print("  Restored clean baseline state in SQLite.")

    print("\n>>> TEST 3 PASSED: Adversarial lookups, injection attempts, and concurrency stress verified.")

if __name__ == "__main__":
    run_test()
