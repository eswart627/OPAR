# scripts/ops/gcp_aws_drift_listener.py
# Purpose: Unified live multi-cloud storage drift listener (AWS S3 + GCP GCS).
# Listens for real-time object moves, renames, and deletions in AWS S3 (via SQS)
# and GCP Cloud Storage (via Pub/Sub), keeping SQLite metadata.db in sync.
#
# Usage:
#   ON:   python scripts/ops/gcp_aws_drift_listener.py
#   OFF:  Press Ctrl + C

import os
import sys
import time
import json
import signal
import threading
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Force unbuffered output and utf-8 encoding for console compatibility
sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

from src.stores.metadata_store import MetadataStore

# AWS Credentials and Configuration
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ACCESS_KEY = os.environ.get("AWS_ADMIN_ACCESS_KEY", "")
AWS_SECRET_KEY = os.environ.get("AWS_ADMIN_SECRET_KEY", "")
AWS_QUEUE_NAME = "permission-rag-drift-queue"

# GCP Credentials and Configuration
GCP_ADMIN_KEY_PATH = os.environ.get("GCP_ADMIN_KEY_PATH", "")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_SUB_NAME = f"projects/{GCP_PROJECT_ID}/subscriptions/permission-rag-gcp-drift-sub" if GCP_PROJECT_ID else ""

DB_PATH = os.path.join(project_root, "artifacts", "metadata.db")


# ==============================================================================
# AWS S3 / SQS LISTENER THREAD
# ==============================================================================
def run_aws_listener(stop_event: threading.Event, ms: MetadataStore):
    """Listens to AWS S3 storage drift notifications via SQS."""
    import boto3

    try:
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
        queue_url = sqs.get_queue_url(QueueName=AWS_QUEUE_NAME)["QueueUrl"]
    except Exception as e:
        print(f"[AWS LISTENER ERROR] Failed to connect to AWS SQS: {e}")
        return

    print(f"  [AWS] Listening on SQS Queue: {queue_url}")

    while not stop_event.is_set():
        try:
            resp = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=2,  # 2s short-poll to stay responsive to stop signals
            )
        except Exception:
            if stop_event.is_set():
                break
            time.sleep(1)
            continue

        msgs = resp.get("Messages", [])
        if not msgs:
            continue

        for msg in msgs:
            receipt = msg["ReceiptHandle"]
            try:
                body = json.loads(msg["Body"])
                records = body.get("Records", [])
                if not records:
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                    continue

                record = records[0]
                event_name = record.get("eventName", "Unknown")
                bucket = record.get("s3", {}).get("bucket", {}).get("name")
                key = record.get("s3", {}).get("object", {}).get("key")

                print("\n" + "=" * 75)
                print(f"[LIVE AWS EVENT] {time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"  * Event:  {event_name}")
                print(f"  * Bucket: {bucket}")
                print(f"  * Object: {key}")

                # Retrieve immutable UUID tag stamped on the S3 object
                try:
                    head = s3.head_object(Bucket=bucket, Key=key)
                    uuid_val = head.get("Metadata", {}).get("resource_id")
                except Exception as e:
                    print(f"  * [WARN] Cannot inspect S3 object metadata: {e}")
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                    continue

                if not uuid_val:
                    print(f"  * [INFO] Non-RAG object (no resource_id tag). Ignored.")
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                    continue

                print(f"  * Stamped UUID: {uuid_val}")

                # Check SQLite metadata.db
                existing = ms.get_by_uuid(uuid_val)
                if not existing:
                    print(f"  * [WARN] UUID {uuid_val} not found in metadata.db.")
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                    continue

                old_b = (existing.get("parameters") or {}).get("bucket")
                old_f = existing.get("file_name")

                if old_b == bucket and old_f == key:
                    print(f"  * [NO DRIFT] File location matches DB record ({bucket}/{key}).")
                else:
                    print(f"  * [DRIFT DETECTED!]")
                    print(f"      OLD Path in SQLite: s3://{old_b}/{old_f}")
                    print(f"      NEW Path in Cloud:  s3://{bucket}/{key}")

                    t0 = time.perf_counter()
                    ms.update_location(uuid=uuid_val, new_bucket=bucket, new_file_name=key)
                    dur_ms = (time.perf_counter() - t0) * 1000.0

                    print(f"  * [SUCCESS] SQLite metadata.db updated in {dur_ms:.2f} ms!")
                    print(f"  * [INVARIANT] FAISS Vector Store: UNTOUCHED (Zero Re-embedding)!")

                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                print("=" * 75 + "\n")

            except Exception as e:
                print(f"  * [AWS ERROR] Message handling exception: {e}")
                try:
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                except Exception:
                    pass


# ==============================================================================
# GCP GCS / PUB-SUB LISTENER THREAD
# ==============================================================================
def run_gcp_listener(stop_event: threading.Event, ms: MetadataStore):
    """Listens to GCP Cloud Storage drift notifications via Pub/Sub."""
    from google.cloud import storage
    from google.cloud import pubsub_v1

    try:
        subscriber = pubsub_v1.SubscriberClient.from_service_account_json(GCP_ADMIN_KEY_PATH)
        gcp_storage = storage.Client.from_service_account_json(GCP_ADMIN_KEY_PATH)
    except Exception as e:
        print(f"[GCP LISTENER ERROR] Failed to connect to GCP Pub/Sub: {e}")
        return

    print(f"  [GCP] Listening on Pub/Sub: {GCP_SUB_NAME}")

    while not stop_event.is_set():
        try:
            response = subscriber.pull(
                request={"subscription": GCP_SUB_NAME, "max_messages": 1},
                timeout=2.0,  # 2s short-poll to stay responsive to stop signals
            )
        except Exception as e:
            if "DeadlineExceeded" in str(e) or "timeout" in str(e).lower():
                continue
            if stop_event.is_set():
                break
            time.sleep(1)
            continue

        msgs = response.received_messages
        if not msgs:
            continue

        for received in msgs:
            ack_id = received.ack_id
            message = received.message
            data_str = message.data.decode("utf-8")

            try:
                event_data = json.loads(data_str)
            except Exception:
                subscriber.acknowledge(request={"subscription": GCP_SUB_NAME, "ack_ids": [ack_id]})
                continue

            bucket = event_data.get("bucket")
            name = event_data.get("name")

            print("\n" + "=" * 75)
            print(f"[LIVE GCP EVENT] {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  * Bucket: {bucket}")
            print(f"  * Object: {name}")

            # Inspect custom metadata for UUID
            custom_meta = event_data.get("metadata") or {}
            uuid_val = custom_meta.get("resource_id")

            if not uuid_val and bucket and name:
                try:
                    blob = gcp_storage.bucket(bucket).get_blob(name)
                    if blob and blob.metadata:
                        uuid_val = blob.metadata.get("resource_id")
                except Exception as e:
                    print(f"  * [WARN] Cannot inspect GCS blob: {e}")

            if not uuid_val:
                print(f"  * [INFO] Non-RAG object or missing resource_id (ignored).")
                subscriber.acknowledge(request={"subscription": GCP_SUB_NAME, "ack_ids": [ack_id]})
                continue

            print(f"  * Stamped UUID: {uuid_val}")

            existing = ms.get_by_uuid(uuid_val)
            if not existing:
                print(f"  * [WARN] UUID {uuid_val} not in metadata.db.")
                subscriber.acknowledge(request={"subscription": GCP_SUB_NAME, "ack_ids": [ack_id]})
                continue

            old_b = (existing.get("parameters") or {}).get("bucket")
            old_f = existing.get("file_name")

            if old_b == bucket and old_f == name:
                print(f"  * [NO DRIFT] File location matches DB record ({bucket}/{name}).")
            else:
                print(f"  * [DRIFT DETECTED!]")
                print(f"      OLD Path in SQLite: gs://{old_b}/{old_f}")
                print(f"      NEW Path in Cloud:  gs://{bucket}/{name}")

                t0 = time.perf_counter()
                ms.update_location(uuid=uuid_val, new_bucket=bucket, new_file_name=name)
                dur_ms = (time.perf_counter() - t0) * 1000.0

                print(f"  * [SUCCESS] SQLite metadata.db updated in {dur_ms:.2f} ms!")
                print(f"  * [INVARIANT] FAISS Vector Store: UNTOUCHED (Zero Re-embedding)!")

            subscriber.acknowledge(request={"subscription": GCP_SUB_NAME, "ack_ids": [ack_id]})
            print("=" * 75 + "\n")


# ==============================================================================
# MAIN RUNNER
# ==============================================================================
def main():
    stop_event = threading.Event()
    ms = MetadataStore()
    ms.load(DB_PATH)

    print("=" * 80)
    print("  MULTI-CLOUD DRIFT LISTENER (AWS S3 + GCP GCS)")
    print(f"  Started at:  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Target DB:   {DB_PATH}")
    print("  Status:      [ON] - Actively monitoring AWS SQS and GCP Pub/Sub...")
    print("  To Stop:     Press Ctrl + C")
    print("=" * 80 + "\n")

    t_aws = threading.Thread(
        target=run_aws_listener,
        args=(stop_event, ms),
        daemon=True,
        name="AWS-DriftListener",
    )
    t_gcp = threading.Thread(
        target=run_gcp_listener,
        args=(stop_event, ms),
        daemon=True,
        name="GCP-DriftListener",
    )

    t_aws.start()
    t_gcp.start()

    def handle_shutdown(signum, frame):
        print("\n[STOP SIGNAL] Shutting down multi-cloud drift listener...")
        stop_event.set()
        t_aws.join(timeout=3)
        t_gcp.join(timeout=3)
        print("[OFF] Multi-cloud drift listener has stopped cleanly.\n")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        while t_aws.is_alive() or t_gcp.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        handle_shutdown(None, None)


if __name__ == "__main__":
    main()
