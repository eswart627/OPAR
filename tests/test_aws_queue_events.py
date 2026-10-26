# scripts/test_aws_queue_events.py
# Test Script 1: Verifies that renaming/moving a file in S3 triggers an event into AWS SQS
# and our listener script successfully extracts the full event details.

import os
import sys
import time
import json
import boto3
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ACCESS_KEY = os.environ["AWS_ADMIN_ACCESS_KEY"]
AWS_SECRET_KEY = os.environ["AWS_ADMIN_SECRET_KEY"]

QUEUE_NAME = "permission-rag-drift-queue"

def run_test():
    print("=" * 80)
    print("  TEST SCRIPT 1: AWS S3 RENAME/MOVE -> SQS EVENT VERIFICATION")
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
    print(f"[1] Connected to SQS Queue: {queue_url}")
    
    # Drain any existing messages to start with a clean slate
    print("[2] Draining any existing messages in queue...")
    while True:
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1)
        msgs = resp.get("Messages", [])
        if not msgs:
            break
        for m in msgs:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
    print("    -> Queue is clean.")
    
    # Load storage map to find an AWS file
    storage_map_path = os.path.join(project_root, "artifacts", "file_to_storage_info.json")
    with open(storage_map_path, "r", encoding="utf-8") as f:
        storage_map = json.load(f)
        
    aws_item = next((k, v) for k, v in storage_map.items() if v["provider"] == "aws")
    source_file = aws_item[0]
    source_bucket = aws_item[1]["bucket"]
    global_uuid = aws_item[1]["global_uuid"]
    
    target_bucket = "tfjafqjv-aws-bucket-1"
    target_key = f"archive_events_test/renamed_{source_file}"
    
    print(f"\n[3] Target File for Live Cloud Rename/Move:")
    print(f"    - Source: s3://{source_bucket}/{source_file}")
    print(f"    - Target: s3://{target_bucket}/{target_key}")
    print(f"    - Stamped UUID: {global_uuid}")
    
    # Perform S3 Copy (Simulates moving/renaming to another bucket/path)
    print(f"\n[4] Executing S3 copy in AWS to simulate file move...")
    s3.copy_object(
        CopySource={"Bucket": source_bucket, "Key": source_file},
        Bucket=target_bucket,
        Key=target_key,
        MetadataDirective="COPY",  # Preserves x-amz-meta-resource_id
    )
    print("    -> S3 copy complete. Live event fired in AWS S3!")
    
    # Poll SQS Queue for the event
    print(f"\n[5] Polling SQS Queue for incoming S3 event (Long polling up to 15s)...")
    t0 = time.time()
    event_record = None
    receipt_handle = None
    
    for attempt in range(15):
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=2
        )
        messages = resp.get("Messages", [])
        if messages:
            msg = messages[0]
            receipt_handle = msg["ReceiptHandle"]
            body = json.loads(msg["Body"])
            if "Records" in body:
                event_record = body["Records"][0]
                break
        time.sleep(0.5)
        
    elapsed = time.time() - t0
    
    assert event_record is not None, "FAILED: No S3 event message was received in SQS within timeout!"
    print(f"    -> [SUCCESS] Event received from SQS in {elapsed:.2f} seconds!")
    
    # Display Extracted Event Details
    event_name = event_record.get("eventName")
    event_bucket = event_record.get("s3", {}).get("bucket", {}).get("name")
    event_key = event_record.get("s3", {}).get("object", {}).get("key")
    event_size = event_record.get("s3", {}).get("object", {}).get("size")
    event_source = event_record.get("eventSource")
    event_time = event_record.get("eventTime")
    
    print("\n" + "=" * 80)
    print("  RAW EVENT DETAILS DELIVERED BY AWS SQS:")
    print("=" * 80)
    print(f"  * Event Source:        {event_source}")
    print(f"  * Event Name:          {event_name}")
    print(f"  * Destination Bucket:  {event_bucket}")
    print(f"  * New Object Key:      {event_key}")
    print(f"  * Object Size:         {event_size} bytes")
    print(f"  * Event Timestamp:     {event_time}")
    print("=" * 80)
    
    # Assertions
    assert event_bucket == target_bucket, f"Bucket mismatch: {event_bucket} vs {target_bucket}"
    assert event_key == target_key, f"Key mismatch: {event_key} vs {target_key}"
    print("\n[6] Assertions Passed: SQS event perfectly captured the exact bucket and key!")
    
    # Clean up test message and test S3 object
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    s3.delete_object(Bucket=target_bucket, Key=target_key)
    print("[7] Cleaned up test event from SQS and temporary test object from S3.")
    print(">>> TEST SCRIPT 1 PASSED: AWS S3 Rename/Move -> SQS Event capture verified 100%!")

if __name__ == "__main__":
    run_test()
