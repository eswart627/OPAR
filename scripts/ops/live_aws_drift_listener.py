# scripts/live_drift_listener.py
# Real-time listener: Waits for manual or automated file moves in AWS S3,
# catches the event via SQS, discovers the UUID, and updates SQLite metadata.db live.

import os
import sys
import time
import json
import boto3
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True)

load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

from src.stores.metadata_store import MetadataStore

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ACCESS_KEY = os.environ["AWS_ADMIN_ACCESS_KEY"]
AWS_SECRET_KEY = os.environ["AWS_ADMIN_SECRET_KEY"]

QUEUE_NAME = "permission-rag-drift-queue"

def start_listener(single_event_mode=False):
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
    
    db_path = os.path.join(project_root, "artifacts", "metadata.db")
    ms = MetadataStore()
    ms.load(db_path)
    
    print("=" * 85)
    print("  LIVE CLOUD STORAGE DRIFT LISTENER (AWS S3 -> SQS -> SQLITE)")
    print("=" * 85)
    print(f"Connected to SQS Queue: {queue_url}")
    print(f"Tracking SQLite DB:    {db_path}")
    print("\n>>> LISTENER IS ACTIVE! Go to your AWS S3 Console and Move/Rename a file now!")
    print("=" * 85 + "\n")
    
    events_processed = 0
    
    try:
        while True:
            # Long poll SQS (waits up to 20s per poll)
            resp = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20
            )
            
            messages = resp.get("Messages", [])
            if not messages:
                print("  [Waiting...] Still listening for file moves in AWS...")
                continue
                
            for msg in messages:
                receipt = msg["ReceiptHandle"]
                body = json.loads(msg["Body"])
                
                records = body.get("Records", [])
                if not records:
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                    continue
                    
                record = records[0]
                event_name = record.get("eventName")
                bucket = record.get("s3", {}).get("bucket", {}).get("name")
                key = record.get("s3", {}).get("object", {}).get("key")
                
                print("\n" + "#" * 85)
                print(f"[LIVE AWS EVENT CAUGHT!] {time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"  * Event Name:     {event_name}")
                print(f"  * Current Bucket: {bucket}")
                print(f"  * Object Key:     {key}")
                
                # Check object metadata in S3
                try:
                    head = s3.head_object(Bucket=bucket, Key=key)
                    uuid = head.get("Metadata", {}).get("resource_id")
                except Exception as e:
                    print(f"  * [WARN] Could not inspect object in S3: {e}")
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                    continue
                    
                if not uuid:
                    print(f"  * [INFO] Object does not have a resource_id UUID tag (ignoring non-RAG object).")
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                    continue
                    
                print(f"  * Stamped UUID:   {uuid}")
                
                # Look up existing record in SQLite metadata.db
                existing = ms.get_by_uuid(uuid)
                if not existing:
                    print(f"  * [WARN] UUID {uuid} not found in metadata.db.")
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                    continue
                    
                old_bucket = (existing.get("parameters") or {}).get("bucket")
                old_file = existing.get("file_name")
                
                if old_bucket == bucket and old_file == key:
                    print(f"  * [NO DRIFT] File is already at recorded location ({bucket}/{key}).")
                else:
                    print(f"  * [DRIFT DETECTED!]")
                    print(f"      OLD Path in SQLite: s3://{old_bucket}/{old_file}")
                    print(f"      NEW Path in Cloud:  s3://{bucket}/{key}")
                    
                    t0 = time.perf_counter()
                    ms.update_location(uuid=uuid, new_bucket=bucket, new_file_name=key)
                    t_ms = (time.perf_counter() - t0) * 1000.0
                    
                    print(f"  * >>> SUCCESS: SQLite metadata.db updated atomically in {t_ms:.2f} ms!")
                    print(f"  * >>> FAISS Vector Store: UNTOUCHED (Zero Re-embedding needed)!")
                    
                # Delete message from queue
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                events_processed += 1
                print("#" * 85 + "\n")
                
                if single_event_mode:
                    print(f"[DONE] Single event processed successfully. Exiting listener.")
                    return
                    
    except KeyboardInterrupt:
        print("\n[STOPPED] Listener stopped by user.")

if __name__ == "__main__":
    single_mode = "--single" in sys.argv
    start_listener(single_event_mode=single_mode)
