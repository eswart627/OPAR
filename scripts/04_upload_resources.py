# 04_upload_resources.py
# Purpose: Upload prepared files in ./resources to GCP/AWS buckets based on file_to_storage_info.json
# Note: Business logic preserved. Comments normalized; [INFO]/[ERROR] log tags.

import os
import sys
import json
from pathlib import Path
from google.cloud import storage as gcp_storage
import boto3

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# ---------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------

# Load environment variables from config/.env
from src.env_loader import load_project_env
load_project_env()

GCP_ADMIN_KEY_PATH = os.environ.get("GCP_ADMIN_KEY_PATH", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ADMIN_ACCESS_KEY = os.environ["AWS_ADMIN_ACCESS_KEY"]
AWS_ADMIN_SECRET_KEY = os.environ["AWS_ADMIN_SECRET_KEY"]

ARTIFACTS_DIR = Path("artifacts")

STORAGE_MAP_FILE = ARTIFACTS_DIR / "file_to_storage_info.json"
RESOURCE_DIR = ARTIFACTS_DIR / "resources"

# ---------------------------------------------------------------------
# SDK clients
# ---------------------------------------------------------------------

# GCP
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "project-444c1d0a-6028-4d76-8d5")
if GCP_ADMIN_KEY_PATH and os.path.exists(GCP_ADMIN_KEY_PATH):
    gcp_client = gcp_storage.Client.from_service_account_json(GCP_ADMIN_KEY_PATH)
else:
    gcp_client = gcp_storage.Client(project=GCP_PROJECT_ID)

# AWS
s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ADMIN_ACCESS_KEY,
    aws_secret_access_key=AWS_ADMIN_SECRET_KEY,
    region_name=AWS_REGION,
)

from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# ---------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------

def upload_to_gcp(bucket_name: str, file_path: str, blob_name: str, global_uuid: str = "") -> bool:
    """Upload a single file to a GCS bucket with resource_id metadata."""
    try:
        bucket = gcp_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        if global_uuid:
            blob.metadata = {"resource_id": global_uuid}
        blob.upload_from_filename(file_path)
        return True
    except Exception as e:
        print(f"[GCP][ERROR] Failed {blob_name} -> {bucket_name}: {e}")
        return False

def upload_to_aws(bucket_name: str, file_path: str, key: str, global_uuid: str = "") -> bool:
    """Upload a single file to an S3 bucket with resource_id metadata."""
    try:
        extra_args = {}
        if global_uuid:
            extra_args["Metadata"] = {"resource_id": global_uuid}
        s3_client.upload_file(file_path, bucket_name, key, ExtraArgs=extra_args if extra_args else None)
        return True
    except Exception as e:
        print(f"[AWS][ERROR] Failed {key} -> {bucket_name}: {e}")
        return False

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def _upload_task(item):
    file_name, info = item
    provider = info["provider"]
    bucket = info["bucket"]
    global_uuid = info.get("global_uuid", "")
    file_path = os.path.join(RESOURCE_DIR, provider.upper(), bucket, file_name)

    if not os.path.exists(file_path):
        return False

    if provider == "gcp":
        return upload_to_gcp(bucket, file_path, file_name, global_uuid=global_uuid)
    elif provider == "aws":
        return upload_to_aws(bucket, file_path, file_name, global_uuid=global_uuid)
    return False

def upload_all(max_workers: int = 30) -> None:
    """Upload all files under ./resources according to the storage map with concurrency."""
    with open(STORAGE_MAP_FILE, "r", encoding="utf-8") as f:
        storage_map = json.load(f)

    total_files = len(storage_map)
    print(f"[INFO] Starting upload of {total_files} files across 10 buckets (Workers: {max_workers})...")

    items = list(storage_map.items())
    completed = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_upload_task, item): item for item in items}
        
        for future in as_completed(futures):
            res = future.result()
            if res:
                completed += 1
            else:
                errors += 1
            
            # Print live update every 500 files or at completion
            if completed % 500 == 0 or (completed + errors) == total_files:
                pct = ((completed + errors) / total_files) * 100
                print(f"[UPLOAD PROGRESS] {completed + errors}/{total_files} ({pct:.1f}%) uploaded | Success: {completed}, Errors: {errors}")

    print(f"[INFO] All uploads finished! Total successfully uploaded: {completed}/{total_files}")

# ---------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------

if __name__ == "__main__":
    upload_all()
