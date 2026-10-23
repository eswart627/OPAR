import os
import sys
import json
import time
import random
import string
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import storage

# Load .env
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=True)

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "project-444c1d0a-6028-4d76-8d5")
KEY_PATH = os.environ.get("GCP_ADMIN_KEY_PATH", "./artifacts/Permission_Aware_Rag_GCP_ADMIN_KEY.json")
LOCATION = os.environ.get("GCP_BUCKET_LOCATION", "asia-south1")

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"
RECORD_FILE = ARTIFACTS_DIR / "mock_test_buckets.json"

def get_storage_client() -> storage.Client:
    key_file = Path(KEY_PATH)
    if not key_file.is_absolute():
        key_file = (Path(__file__).resolve().parent.parent / key_file).resolve()
    
    if not key_file.exists():
        raise FileNotFoundError(f"GCP Admin Key not found at: {key_file}")
    
    return storage.Client.from_service_account_json(str(key_file))

def create_and_store():
    client = get_storage_client()
    print(f"[GCP-TEST][INFO] Authenticated as: {client.get_service_account_email()}")
    print(f"[GCP-TEST][INFO] Target Project: {PROJECT_ID}, Location: {LOCATION}")

    # Generate a random 6-character unique suffix to ensure global bucket uniqueness
    rand_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    bucket_names = [f"mock-parag-{rand_suffix}-bucket-{i+1}" for i in range(6)]

    created_buckets = []

    print("\n--- [Step 1: Creating 6 GCP Buckets] ---")
    for name in bucket_names:
        print(f"[GCP-TEST][INFO] Creating bucket: {name} in {LOCATION}...")
        try:
            bucket = client.create_bucket(name, project=PROJECT_ID, location=LOCATION)
            created_buckets.append(name)
            print(f"[GCP-TEST][SUCCESS] Bucket created: {bucket.name}")
        except Exception as e:
            print(f"[GCP-TEST][ERROR] Failed to create bucket {name}: {e}")

    print("\n--- [Step 2: Uploading Sample Files to Each Bucket] ---")
    for name in created_buckets:
        try:
            bucket = client.bucket(name)
            
            # 1. Sample text file
            txt_blob = bucket.blob("sample_note.txt")
            txt_blob.upload_from_string(f"Hello from Permission Aware RAG mock test!\nBucket: {name}\nTime: {time.ctime()}")
            
            # 2. Sample JSON file
            json_blob = bucket.blob("sample_data.json")
            sample_data = {
                "bucket": name,
                "project": PROJECT_ID,
                "created_at": time.time(),
                "status": "active",
                "mock_chunks": [
                    {"id": 1, "text": "This is a permission-protected retrieval test chunk."},
                    {"id": 2, "text": "Access granted via GCP Storage Admin Service Account."}
                ]
            }
            json_blob.upload_from_string(json.dumps(sample_data, indent=2), content_type="application/json")
            print(f"[GCP-TEST][SUCCESS] Uploaded 'sample_note.txt' & 'sample_data.json' to: {name}")
        except Exception as e:
            print(f"[GCP-TEST][ERROR] Failed upload to {name}: {e}")

    # Save created bucket list locally
    RECORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RECORD_FILE, "w", encoding="utf-8") as f:
        json.dump(created_buckets, f, indent=2)
    
    print(f"\n[GCP-TEST][DONE] Successfully created {len(created_buckets)}/6 buckets and stored files.")
    print(f"[GCP-TEST][INFO] Created bucket list saved to: {RECORD_FILE.name}")
    print("\nYou can now check your Google Cloud Console (Cloud Storage > Buckets)!")

def delete_buckets():
    client = get_storage_client()
    if not RECORD_FILE.exists():
        print(f"[GCP-TEST][WARN] No record file found at {RECORD_FILE}. Nothing to delete.")
        return

    with open(RECORD_FILE, "r", encoding="utf-8") as f:
        buckets_to_delete = json.load(f)

    if not buckets_to_delete:
        print("[GCP-TEST][INFO] Bucket record is empty.")
        return

    print(f"\n--- [Deleting {len(buckets_to_delete)} Mock Buckets and Their Contents] ---")
    for name in buckets_to_delete:
        try:
            bucket = client.get_bucket(name)
            # Delete all blobs in bucket first
            blobs = list(bucket.list_blobs())
            for blob in blobs:
                blob.delete()
                print(f"[GCP-TEST][INFO] Deleted blob {blob.name} from {name}")
            # Delete bucket
            bucket.delete()
            print(f"[GCP-TEST][SUCCESS] Deleted bucket: {name}")
        except Exception as e:
            print(f"[GCP-TEST][ERROR] Error deleting bucket {name}: {e}")

    # Remove record file
    RECORD_FILE.unlink(missing_ok=True)
    print("\n[GCP-TEST][DONE] Cleanup complete.")

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "--create"
    if action == "--create":
        create_and_store()
    elif action == "--delete":
        delete_buckets()
    else:
        print("Usage: python test_gcp_mock_pipeline.py [--create | --delete]")

