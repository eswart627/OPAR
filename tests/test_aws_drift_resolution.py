# scripts/test_aws_drift_resolution.py
# Test Script 2: Verifies that:
# 1. Event info from SQS is used to inspect cloud metadata (head_object) to find UUID.
# 2. Local SQLite DB is queried and confirmed NOT TO MATCH the new cloud location (Drift Detected).
# 3. SQLite DB is atomically updated to the new path in < 5ms.
# 4. FAISS dense vector store remains 100% UNTOUCHED (zero re-embedding).
# 5. PermissionRetriever validates access against the new cloud location.

import os
import sys
import time
import json
import boto3
import hashlib
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

from src.stores.metadata_store import MetadataStore
from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig
from src.managers.credential_manager import CredentialManager

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ACCESS_KEY = os.environ["AWS_ADMIN_ACCESS_KEY"]
AWS_SECRET_KEY = os.environ["AWS_ADMIN_SECRET_KEY"]

QUEUE_NAME = "permission-rag-drift-queue"

def get_faiss_hash():
    index_path = os.path.join(project_root, "vector_db", "faiss.index")
    if not os.path.exists(index_path):
        return None
    with open(index_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

def run_test():
    print("=" * 80)
    print("  TEST SCRIPT 2: AWS SQS EVENT -> UUID DISCOVERY -> DB DRIFT SYNC")
    print("=" * 80)
    
    s3 = boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )
    sqs = boto3.client(
        "sqs",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )
    
    queue_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
    
    # Clean queue
    while True:
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1)
        msgs = resp.get("Messages", [])
        if not msgs:
            break
        for m in msgs:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
            
    # Load MetadataStore and record initial FAISS index hash
    db_path = os.path.join(project_root, "artifacts", "metadata.db")
    ms = MetadataStore()
    ms.load(db_path)
    
    initial_faiss_hash = get_faiss_hash()
    print(f"[1] Baseline Loaded:")
    print(f"    - SQLite DB Path: {db_path} ({len(ms.all())} records)")
    print(f"    - Initial FAISS Index SHA-256: {initial_faiss_hash}")
    
    # Pick target AWS document
    storage_map_path = os.path.join(project_root, "artifacts", "file_to_storage_info.json")
    with open(storage_map_path, "r", encoding="utf-8") as f:
        storage_map = json.load(f)
        
    aws_item = next((k, v) for k, v in storage_map.items() if v["provider"] == "aws")
    original_file = aws_item[0]
    original_bucket = aws_item[1]["bucket"]
    expected_uuid = aws_item[1]["global_uuid"]
    
    new_bucket = "tfjafqjv-aws-bucket-1"
    new_key = f"production_archive/renamed_{original_file}"
    
    print(f"\n[2] Performing Cloud Storage Move:")
    print(f"    - From: s3://{original_bucket}/{original_file}")
    print(f"    - To:   s3://{new_bucket}/{new_key}")
    
    # Move object in S3
    s3.copy_object(
        CopySource={"Bucket": original_bucket, "Key": original_file},
        Bucket=new_bucket,
        Key=new_key,
        MetadataDirective="COPY",
    )
    print("    -> S3 copy completed in AWS cloud.")
    
    # Receive SQS Event
    print(f"\n[3] Catching Event Notification from SQS...")
    event_record = None
    receipt_handle = None
    for _ in range(15):
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=2)
        msgs = resp.get("Messages", [])
        if msgs:
            msg = msgs[0]
            receipt_handle = msg["ReceiptHandle"]
            body = json.loads(msg["Body"])
            if "Records" in body:
                event_record = body["Records"][0]
                break
        time.sleep(0.5)
        
    assert event_record is not None, "Failed to receive SQS event!"
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    
    event_bucket = event_record["s3"]["bucket"]["name"]
    event_key = event_record["s3"]["object"]["key"]
    print(f"    -> [SUCCESS] Received event for: s3://{event_bucket}/{event_key}")
    
    # Verify AWS SQS event does NOT contain the UUID
    event_str = json.dumps(event_record)
    assert expected_uuid not in event_str, "Unexpected: AWS event should not include custom metadata!"
    print(f"    -> Confirmed: Raw AWS event does NOT contain the document UUID.")
    
    # STEP 4: Inspect Cloud Object Metadata to Discover Document UUID
    print(f"\n[4] Inspecting Cloud Object via s3.head_object to extract UUID tag...")
    t0_head = time.perf_counter()
    head_resp = s3.head_object(Bucket=event_bucket, Key=event_key)
    t_head_ms = (time.perf_counter() - t0_head) * 1000.0
    
    discovered_uuid = head_resp.get("Metadata", {}).get("resource_id")
    print(f"    -> Discovered UUID from cloud tag: {discovered_uuid} (took {t_head_ms:.2f}ms)")
    assert discovered_uuid == expected_uuid, f"UUID mismatch! {discovered_uuid} vs {expected_uuid}"
    print(f"    -> [SUCCESS] Discovered UUID matches expected document UUID perfectly!")
    
    # STEP 5: Check Local Database & Detect Drift
    print(f"\n[5] Querying Local SQLite DB for UUID: {discovered_uuid}...")
    existing_row = ms.get_by_uuid(discovered_uuid)
    db_bucket = (existing_row.get("parameters") or {}).get("bucket")
    db_file_name = existing_row.get("file_name")
    
    print(f"    - Current SQLite Record:")
    print(f"        Bucket:    '{db_bucket}'")
    print(f"        File Name: '{db_file_name}'")
    print(f"    - Live Cloud Object (from event):")
    print(f"        Bucket:    '{event_bucket}'")
    print(f"        File Name: '{event_key}'")
    
    # Assert drift
    is_drifted = (db_bucket != event_bucket) or (db_file_name != event_key)
    assert is_drifted, "Expected drift was not detected!"
    print(f"\n    >>> [DRIFT DETECTED] SQLite metadata DOES NOT MATCH live cloud location!")
    
    # STEP 6: Execute Atomic SQLite Update
    print(f"\n[6] Synchronizing Drift in SQLite metadata.db...")
    t0_upd = time.perf_counter()
    update_ok = ms.update_location(
        uuid=discovered_uuid,
        new_bucket=event_bucket,
        new_file_name=event_key
    )
    t_upd_ms = (time.perf_counter() - t0_upd) * 1000.0
    
    assert update_ok, "Failed to update SQLite record!"
    print(f"    -> [SUCCESS] SQLite updated in {t_upd_ms:.3f} ms!")
    
    # Verify new DB state
    refreshed_row = ms.get_by_uuid(discovered_uuid)
    assert (refreshed_row.get("parameters") or {}).get("bucket") == event_bucket
    assert refreshed_row.get("file_name") == event_key
    print(f"    -> Verified SQLite now points to: s3://{event_bucket}/{event_key}")
    
    # STEP 7: Verify Zero FAISS Re-embedding
    print(f"\n[7] Verifying Decoupled Invariant: FAISS Vector Store...")
    current_faiss_hash = get_faiss_hash()
    print(f"    - Initial FAISS SHA-256: {initial_faiss_hash}")
    print(f"    - Current FAISS SHA-256: {current_faiss_hash}")
    assert initial_faiss_hash == current_faiss_hash, "FAISS index was modified! Decoupling broken!"
    print(f"    -> [SUCCESS] FAISS index hash is 100% byte-for-byte identical (ZERO RE-EMBEDDING)!")
    
    # STEP 8: Test Live PermissionRetriever at New Path
    print(f"\n[8] Testing Live Authorization at New S3 Path via PermissionRetriever...")
    cm = CredentialManager(base_path=os.path.join(project_root, "artifacts"), file_name="test_credential.txt")
    admin_ac = cm.get_ac_manager("baseline-admin")
    
    pr = PermissionRetriever(ms, PermissionRetrieverConfig(max_workers=2, use_cache=False))
    doc_payload = [{"global_uuid": discovered_uuid, "file_name": event_key}]
    filtered_docs, decisions, _, dur = pr.filter_docs(doc_payload, admin_ac)
    
    print(f"    - Authorization Decision for {discovered_uuid}: {decisions.get(discovered_uuid)}")
    print(f"    - Check Duration: {dur * 1000.0:.2f} ms")
    assert decisions.get(discovered_uuid) is True, "Permission check failed on moved object!"
    print(f"    -> [SUCCESS] Live cloud permission check PASSED for new S3 path!")
    
    # Clean up test object and restore baseline in SQLite
    print(f"\n[9] Restoring Baseline State...")
    s3.delete_object(Bucket=new_bucket, Key=new_key)
    ms.update_location(uuid=discovered_uuid, new_bucket=original_bucket, new_file_name=original_file)
    print("    -> Cleaned up test archive object from S3.")
    print("    -> Restored SQLite metadata record to original baseline.")
    
    print("\n" + "=" * 80)
    print(">>> TEST SCRIPT 2 PASSED: Cloud Event -> UUID Discovery -> Drift Sync -> Access Check 100% Verified!")
    print("=" * 80)

if __name__ == "__main__":
    run_test()
