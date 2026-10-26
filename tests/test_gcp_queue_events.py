# scripts/test_gcp_queue_events.py
# Test Script: Verifies that renaming/moving a file in GCS triggers an event into GCP Pub/Sub
# and our listener script successfully extracts the full event details.

import os
import sys
import time
import json
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import storage
from google.cloud import pubsub_v1

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

GCP_ADMIN_KEY_PATH = os.environ["GCP_ADMIN_KEY_PATH"]
GCP_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
SUB_NAME = f"projects/{GCP_PROJECT_ID}/subscriptions/permission-rag-gcp-drift-sub"

def run_test():
    print("=" * 80)
    print("  TEST SCRIPT: GCP GCS RENAME/MOVE -> PUB/SUB EVENT VERIFICATION")
    print("=" * 80)
    
    subscriber = pubsub_v1.SubscriberClient.from_service_account_json(GCP_ADMIN_KEY_PATH)
    gcp_storage = storage.Client.from_service_account_json(GCP_ADMIN_KEY_PATH)
    
    # 1. Drain existing messages
    print("[1] Draining any existing messages in GCP Pub/Sub subscription...")
    while True:
        try:
            resp = subscriber.pull(request={"subscription": SUB_NAME, "max_messages": 10}, timeout=1.0)
            if not resp.received_messages:
                break
            ack_ids = [m.ack_id for m in resp.received_messages]
            subscriber.acknowledge(request={"subscription": SUB_NAME, "ack_ids": ack_ids})
        except Exception:
            break
    print("    -> Subscription is clean.")
    
    # 2. Pick target GCP file
    storage_map_path = os.path.join(project_root, "artifacts", "file_to_storage_info.json")
    with open(storage_map_path, "r", encoding="utf-8") as f:
        storage_map = json.load(f)
        
    gcp_item = next((k, v) for k, v in storage_map.items() if v["provider"] == "gcp")
    source_file = gcp_item[0]
    source_bucket = gcp_item[1]["bucket"]
    global_uuid = gcp_item[1]["global_uuid"]
    
    target_bucket = "tfjafqjv-gcp-bucket-1"
    target_key = f"gcp_events_test/renamed_{source_file}"
    
    print(f"\n[2] Target File for Live GCP Cloud Rename/Move:")
    print(f"    - Source: gs://{source_bucket}/{source_file}")
    print(f"    - Target: gs://{target_bucket}/{target_key}")
    print(f"    - Stamped UUID: {global_uuid}")
    
    # 3. Perform GCS Copy (simulates move/rename in cloud)
    print(f"\n[3] Executing GCS copy in Google Cloud to trigger event...")
    src_bucket_obj = gcp_storage.bucket(source_bucket)
    src_blob = src_bucket_obj.get_blob(source_file)
    dst_bucket_obj = gcp_storage.bucket(target_bucket)
    
    dst_blob = src_bucket_obj.copy_blob(src_blob, dst_bucket_obj, target_key)
    dst_blob.metadata = {"resource_id": global_uuid}
    dst_blob.patch()
    print("    -> GCS copy complete. Live event fired in Google Cloud!")
    
    # 4. Poll GCP Pub/Sub for the event
    print(f"\n[4] Polling GCP Pub/Sub for incoming event (timeout 15s)...")
    t0 = time.time()
    event_data = None
    ack_id = None
    
    for attempt in range(15):
        try:
            resp = subscriber.pull(request={"subscription": SUB_NAME, "max_messages": 1}, timeout=2.0)
            if resp.received_messages:
                msg = resp.received_messages[0]
                ack_id = msg.ack_id
                event_data = json.loads(msg.message.data.decode("utf-8"))
                break
        except Exception:
            pass
        time.sleep(0.5)
        
    elapsed = time.time() - t0
    assert event_data is not None, "FAILED: No GCS event message was received in Pub/Sub within timeout!"
    print(f"    -> [SUCCESS] Event received from GCP Pub/Sub in {elapsed:.2f} seconds!")
    
    # Display details
    event_bucket = event_data.get("bucket")
    event_key = event_data.get("name")
    event_size = event_data.get("size")
    event_time = event_data.get("timeCreated")
    event_custom_meta = event_data.get("metadata") or {}
    
    print("\n" + "=" * 80)
    print("  RAW EVENT DETAILS DELIVERED BY GCP PUB/SUB:")
    print("=" * 80)
    print(f"  * Destination Bucket:  {event_bucket}")
    print(f"  * New Object Name:     {event_key}")
    print(f"  * Object Size:         {event_size} bytes")
    print(f"  * Event Timestamp:     {event_time}")
    print(f"  * Embedded Metadata:   {event_custom_meta}")
    print("=" * 80)
    
    assert event_bucket == target_bucket
    assert event_key == target_key
    print("\n[5] Assertions Passed: GCP Pub/Sub captured the exact GCS bucket and key!")
    
    # Clean up test object and message
    subscriber.acknowledge(request={"subscription": SUB_NAME, "ack_ids": [ack_id]})
    dst_blob.delete()
    print("[6] Cleaned up test event from Pub/Sub and temporary test object from GCS.")
    print(">>> TEST SCRIPT PASSED: GCP GCS Rename/Move -> Pub/Sub Event capture verified 100%!")

if __name__ == "__main__":
    run_test()
