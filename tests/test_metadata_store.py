# tests/test_metadata_store.py
# Comprehensive Test Suite for SQLite MetadataStore
# Bridges to: src/stores/metadata_store.py
# Combines: Pure DB operations, atomic location updates, and multi-threaded concurrency.

import os
import sys
import time
import json
import sqlite3
import tempfile
import threading
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.stores.metadata_store import MetadataStore


def _create_sqlite_db(db_path: str, records: list):
    """Initializes a valid SQLite metadata table with the required schema and records."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS metadata (
        global_uuid TEXT PRIMARY KEY,
        file_name TEXT,
        provider TEXT,
        bucket TEXT,
        region TEXT,
        endpoint TEXT,
        raw_json TEXT
    );
    """)
    for r in records:
        raw_str = json.dumps(r)
        cur.execute(
            "INSERT INTO metadata (global_uuid, file_name, provider, bucket, region, endpoint, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                r.get("global_uuid", ""),
                r.get("file_name", ""),
                r.get("provider", ""),
                (r.get("parameters") or {}).get("bucket", ""),
                (r.get("parameters") or {}).get("region", ""),
                r.get("endpoint", ""),
                raw_str,
            ),
        )
    conn.commit()
    conn.close()


def test_pure_db_crud():
    """Test 1: Tests database initialization, record loading, querying, and schema validation."""
    print("\n[TEST 1] Testing Pure Database CRUD Operations...")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "test_metadata.db")
        test_uuid = "11111111-2222-3333-4444-555555555555"
        test_doc = {
            "global_uuid": test_uuid,
            "file_name": "original_doc.txt",
            "provider": "aws",
            "endpoint": "https://s3.ap-south-1.amazonaws.com",
            "parameters": {
                "region": "ap-south-1",
                "bucket": "test-aws-bucket-1",
                "iam": "aws iam"
            }
        }
        _create_sqlite_db(db_path, [test_doc])

        ms = MetadataStore()
        ms.load(db_path)

        assert ms.is_sqlite(), "MetadataStore failed to report SQLite mode"
        record = ms.get_by_uuid(test_uuid)
        assert record is not None, "Failed to retrieve record by UUID"
        assert record["file_name"] == "original_doc.txt"
        assert record["parameters"]["bucket"] == "test-aws-bucket-1"

        by_file = ms.get_by_file("original_doc.txt")
        assert by_file is not None
        assert by_file["global_uuid"] == test_uuid

        all_recs = ms.all()
        assert len(all_recs) == 1, f"Expected 1 record, got {len(all_recs)}"
        
        if ms._conn:
            ms._conn.close()
        print("  [PASS] Pure CRUD operations verified successfully.")


def test_atomic_location_update():
    """Test 2: Tests atomic location updates (bucket, file_name, raw_json) in SQLite."""
    print("\n[TEST 2] Testing Atomic Location Updates...")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "test_metadata.db")
        test_uuid = "22222222-3333-4444-5555-666666666666"
        test_doc = {
            "global_uuid": test_uuid,
            "file_name": "before_move.txt",
            "provider": "gcp",
            "endpoint": "https://storage.googleapis.com",
            "parameters": {
                "region": "asia-south1",
                "bucket": "old-gcp-bucket",
                "iam": "google iam"
            }
        }
        _create_sqlite_db(db_path, [test_doc])

        ms = MetadataStore()
        ms.load(db_path)

        # Update location
        t0 = time.perf_counter()
        ms.update_location(uuid=test_uuid, new_bucket="new-gcp-bucket", new_file_name="after_move.txt")
        dur_ms = (time.perf_counter() - t0) * 1000.0

        updated = ms.get_by_uuid(test_uuid)
        assert updated["file_name"] == "after_move.txt", "File name was not updated"
        assert updated["parameters"]["bucket"] == "new-gcp-bucket", "Bucket was not updated"
        assert dur_ms < 50.0, f"Update took too long: {dur_ms:.2f}ms"

        # Verify underlying SQLite table directly
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT file_name, bucket, raw_json FROM metadata WHERE global_uuid = ?", (test_uuid,))
        fn, bk, raw = cur.fetchone()
        conn.close()

        assert fn == "after_move.txt"
        assert bk == "new-gcp-bucket"
        assert json.loads(raw)["file_name"] == "after_move.txt"

        if ms._conn:
            ms._conn.close()
        print(f"  [PASS] Location updated atomically in {dur_ms:.2f} ms.")


def test_concurrent_read_write():
    """Test 3: Tests concurrent readers and writers without database locking errors."""
    print("\n[TEST 3] Testing Concurrent Multi-Threaded Access...")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "test_metadata.db")
        docs = [
            {
                "global_uuid": f"uuid-{i}",
                "file_name": f"doc_{i}.txt",
                "provider": "aws",
                "endpoint": "https://s3.amazonaws.com",
                "parameters": {"bucket": f"bucket-{i % 3}", "region": "ap-south-1"}
            }
            for i in range(20)
        ]
        _create_sqlite_db(db_path, docs)

        errors = []
        open_stores = []

        def reader_task():
            try:
                reader_ms = MetadataStore()
                reader_ms.load(db_path)
                open_stores.append(reader_ms)
                for _ in range(30):
                    res = reader_ms.get_by_uuid("uuid-5")
                    assert res is not None
                    time.sleep(0.002)
            except Exception as e:
                errors.append(f"Reader error: {e}")

        def writer_task():
            try:
                writer_ms = MetadataStore()
                writer_ms.load(db_path)
                open_stores.append(writer_ms)
                for i in range(15):
                    writer_ms.update_location(uuid="uuid-5", new_bucket=f"bucket-updated-{i}", new_file_name="doc_5.txt")
                    time.sleep(0.005)
            except Exception as e:
                errors.append(f"Writer error: {e}")

        threads = [
            threading.Thread(target=reader_task),
            threading.Thread(target=reader_task),
            threading.Thread(target=writer_task),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for s in open_stores:
            if s._conn:
                s._conn.close()

        assert not errors, f"Concurrent execution errors encountered: {errors}"
        print("  [PASS] Concurrent read/write test completed with 0 errors.")


if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING METADATA STORE CONSOLIDATED TEST SUITE")
    print("=" * 80)
    test_pure_db_crud()
    test_atomic_location_update()
    test_concurrent_read_write()
    print("\n" + "=" * 80)
    print(">>> ALL METADATA STORE TESTS PASSED!")
    print("=" * 80)
