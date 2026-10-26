# scripts/live_gcp_drift_listener.py
# Real-time listener: Waits for manual or automated file moves in GCP GCS,
# catches the event via Pub/Sub pull subscription, discovers the UUID, and updates SQLite metadata.db live.

import os
import sys
import time
import json
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import storage
from google.cloud import pubsub_v1

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True)
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

from src.stores.metadata_store import MetadataStore

GCP_ADMIN_KEY_PATH = os.environ["GCP_ADMIN_KEY_PATH"]
GCP_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
SUB_NAME = f"projects/{GCP_PROJECT_ID}/subscriptions/permission-rag-gcp-drift-sub"

def start_gcp_listener(single_event_mode=False):
    subscriber = pubsub_v1.SubscriberClient.from_service_account_json(GCP_ADMIN_KEY_PATH)
    gcp_storage = storage.Client.from_service_account_json(GCP_ADMIN_KEY_PATH)
    
    db_path = os.path.join(project_root, "artifacts", "metadata.db")
    ms = MetadataStore()
    ms.load(db_path)
    
    print("=" * 85)
    print("  LIVE CLOUD STORAGE DRIFT LISTENER (GCP GCS -> PUB/SUB -> SQLITE)")
    print("=" * 85)
    print(f"Subscribed to:     {SUB_NAME}")
    print(f"Tracking SQLite DB: {db_path}")
    print("\n>>> GCP LISTENER IS ACTIVE! Go to your GCP Cloud Storage Console and Move/Rename a file now!")
    print("=" * 85 + "\n")
    
    try:
        while True:
            # Pull messages from GCP Pub/Sub (long-polling with 20s timeout)
            try:
                response = subscriber.pull(
                    request={"subscription": SUB_NAME, "max_messages": 1},
                    timeout=20.0
                )
            except Exception as e:
                # Timeout is normal when no new events arrived
                if "DeadlineExceeded" in str(e) or "timeout" in str(e).lower():
                    print("  [Waiting...] Still listening for file moves in GCP GCS...")
                    continue
                else:
                    print(f"  [ERROR] Pub/Sub pull error: {e}")
                    time.sleep(2)
                    continue
                    
            received_messages = response.received_messages
            if not received_messages:
                continue
                
            for received in received_messages:
                ack_id = received.ack_id
                message = received.message
                data_str = message.data.decode("utf-8")
                
                try:
                    event_data = json.loads(data_str)
                except Exception:
                    subscriber.acknowledge(request={"subscription": SUB_NAME, "ack_ids": [ack_id]})
                    continue
                    
                bucket = event_data.get("bucket")
                name = event_data.get("name")
                
                print("\n" + "#" * 85)
                print(f"[LIVE GCP EVENT CAUGHT!] {time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"  * Bucket: {bucket}")
                print(f"  * Object: {name}")
                
                # Check for UUID in event metadata or fetch via GCS
                custom_meta = event_data.get("metadata") or {}
                uuid = custom_meta.get("resource_id")
                
                if not uuid and bucket and name:
                    try:
                        blob = gcp_storage.bucket(bucket).get_blob(name)
                        if blob and blob.metadata:
                            uuid = blob.metadata.get("resource_id")
                    except Exception as e:
                        print(f"  * [WARN] Error inspecting GCS blob: {e}")
                        
                if not uuid:
                    print(f"  * [INFO] Non-RAG object or missing resource_id tag (ignoring).")
                    subscriber.acknowledge(request={"subscription": SUB_NAME, "ack_ids": [ack_id]})
                    continue
                    
                print(f"  * Stamped UUID: {uuid}")
                
                # Check with SQLite metadata.db
                existing = ms.get_by_uuid(uuid)
                if not existing:
                    print(f"  * [WARN] UUID {uuid} not found in metadata.db.")
                    subscriber.acknowledge(request={"subscription": SUB_NAME, "ack_ids": [ack_id]})
                    continue
                    
                old_bucket = (existing.get("parameters") or {}).get("bucket")
                old_file = existing.get("file_name")
                
                if old_bucket == bucket and old_file == name:
                    print(f"  * [NO DRIFT] File is already at recorded location ({bucket}/{name}).")
                else:
                    print(f"  * [DRIFT DETECTED!]")
                    print(f"      OLD Path in SQLite: gs://{old_bucket}/{old_file}")
                    print(f"      NEW Path in Cloud:  gs://{bucket}/{name}")
                    
                    t0 = time.perf_counter()
                    ms.update_location(uuid=uuid, new_bucket=bucket, new_file_name=name)
                    t_ms = (time.perf_counter() - t0) * 1000.0
                    
                    print(f"  * >>> SUCCESS: SQLite metadata.db updated atomically in {t_ms:.2f} ms!")
                    print(f"  * >>> FAISS Vector Store: UNTOUCHED (Zero Re-embedding needed)!")
                    
                # Acknowledge message
                subscriber.acknowledge(request={"subscription": SUB_NAME, "ack_ids": [ack_id]})
                print("#" * 85 + "\n")
                
                if single_event_mode:
                    print(f"[DONE] Single event processed. Exiting.")
                    return
                    
    except KeyboardInterrupt:
        print("\n[STOPPED] GCP Listener stopped by user.")

if __name__ == "__main__":
    single_mode = "--single" in sys.argv
    start_gcp_listener(single_event_mode=single_mode)
