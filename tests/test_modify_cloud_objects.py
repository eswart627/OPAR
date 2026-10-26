# scripts/test_modify_cloud_objects.py
# Helper script: Performs cloud object moves and renames in AWS S3 and GCP GCS,
# preserving their immutable resource_id UUID tags so drift listeners can detect them.

import os
import sys
import time
import json
import sqlite3
import boto3
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import storage

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ACCESS_KEY = os.environ["AWS_ADMIN_ACCESS_KEY"]
AWS_SECRET_KEY = os.environ["AWS_ADMIN_SECRET_KEY"]

GCP_KEY_PATH = os.environ["GCP_ADMIN_KEY_PATH"]
GCP_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
DB_PATH = os.path.join(project_root, "artifacts", "metadata.db")


def get_existing_file(provider: str):
    """Discovers an existing cloud object that contains a resource_id metadata tag."""
    access_model_path = os.path.join(project_root, "artifacts", "generated_access_model.json")
    with open(access_model_path, "r", encoding="utf-8") as f:
        access_model = json.load(f)

    if provider == "aws":
        s3 = boto3.client(
            "s3",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
        )
        buckets = list(access_model.get("AWS_ABAC", {}).keys())
        for b in buckets:
            resp = s3.list_objects_v2(Bucket=b)
            for item in resp.get("Contents", []):
                key = item["Key"]
                head = s3.head_object(Bucket=b, Key=key)
                uuid_val = head.get("Metadata", {}).get("resource_id")
                if uuid_val:
                    return uuid_val, key, b
        raise RuntimeError("No AWS objects with resource_id found.")

    elif provider == "gcp":
        gcp = storage.Client.from_service_account_json(GCP_KEY_PATH)
        buckets = list(set(access_model.get("GCP_RBAC", {}).values()))
        for b_name in sorted(buckets):
            b = gcp.bucket(b_name)
            for blob in b.list_blobs():
                if blob.metadata and blob.metadata.get("resource_id"):
                    return blob.metadata["resource_id"], blob.name, b_name
        raise RuntimeError("No GCP objects with resource_id found.")


def modify_aws(action="rename"):
    s3 = boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )
    uuid_val, src_key, src_bucket = get_existing_file("aws")
    ts = int(time.time())

    if action == "rename":
        dst_bucket = src_bucket
        dst_key = f"renamed_aws_{ts}.txt"
        print(f"[AWS][ACTION] Renaming object (UUID: {uuid_val}):")
        print(f"      s3://{src_bucket}/{src_key} -> s3://{dst_bucket}/{dst_key}...")
        s3.copy_object(
            CopySource={"Bucket": src_bucket, "Key": src_key},
            Bucket=dst_bucket,
            Key=dst_key,
            MetadataDirective="COPY",
        )
        s3.delete_object(Bucket=src_bucket, Key=src_key)
        print("    -> [DONE] AWS Rename completed!")
        return uuid_val, dst_bucket, dst_key

    elif action == "move":
        # Target a different bucket
        prefix = src_bucket.split("-aws-bucket-")[0]
        curr_num = int(src_bucket.split("-aws-bucket-")[1])
        next_num = 1 if curr_num == 5 else curr_num + 1
        dst_bucket = f"{prefix}-aws-bucket-{next_num}"
        dst_key = f"moved_aws_{ts}.txt"
        
        print(f"[AWS][ACTION] Moving object across buckets (UUID: {uuid_val}):")
        print(f"      s3://{src_bucket}/{src_key} -> s3://{dst_bucket}/{dst_key}...")
        s3.copy_object(
            CopySource={"Bucket": src_bucket, "Key": src_key},
            Bucket=dst_bucket,
            Key=dst_key,
            MetadataDirective="COPY",
        )
        s3.delete_object(Bucket=src_bucket, Key=src_key)
        print("    -> [DONE] AWS Move completed!")
        return uuid_val, dst_bucket, dst_key


def modify_gcp(action="rename"):
    gcp = storage.Client.from_service_account_json(GCP_KEY_PATH)
    uuid_val, src_key, src_bucket_name = get_existing_file("gcp")
    ts = int(time.time())

    src_b = gcp.bucket(src_bucket_name)
    blob = src_b.get_blob(src_key)
    if not blob:
        raise RuntimeError(f"Blob gs://{src_bucket_name}/{src_key} not found in GCS!")

    if action == "rename":
        dst_bucket_name = src_bucket_name
        dst_key = f"renamed_gcp_{ts}.txt"
        print(f"[GCP][ACTION] Renaming object (UUID: {uuid_val}):")
        print(f"      gs://{src_bucket_name}/{src_key} -> gs://{dst_bucket_name}/{dst_key}...")
        dst_blob = src_b.copy_blob(blob, src_b, dst_key)
        if blob.metadata:
            dst_blob.metadata = blob.metadata
            dst_blob.patch()
        blob.delete()
        print("    -> [DONE] GCP Rename completed!")
        return uuid_val, dst_bucket_name, dst_key

    elif action == "move":
        prefix = src_bucket_name.split("-gcp-bucket-")[0]
        curr_num = int(src_bucket_name.split("-gcp-bucket-")[1])
        next_num = 1 if curr_num == 5 else curr_num + 1
        dst_bucket_name = f"{prefix}-gcp-bucket-{next_num}"
        dst_key = f"moved_gcp_{ts}.txt"

        print(f"[GCP][ACTION] Moving object across buckets (UUID: {uuid_val}):")
        print(f"      gs://{src_bucket_name}/{src_key} -> gs://{dst_bucket_name}/{dst_key}...")
        dst_b = gcp.bucket(dst_bucket_name)
        dst_blob = src_b.copy_blob(blob, dst_b, dst_key)
        if blob.metadata:
            dst_blob.metadata = blob.metadata
            dst_blob.patch()
        blob.delete()
        print("    -> [DONE] GCP Move completed!")
        return uuid_val, dst_bucket_name, dst_key


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "aws"
    op = sys.argv[2] if len(sys.argv) > 2 else "rename"

    if target == "aws":
        modify_aws(op)
    elif target == "gcp":
        modify_gcp(op)
    else:
        print("Usage: python test_modify_cloud_objects.py [aws|gcp] [rename|move]")
