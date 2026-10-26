import os
import sys
import json
import boto3
from google.cloud import storage
from google.oauth2 import service_account

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")

ROOT_DIR = r"c:\Users\pichi\Desktop\Permission Aware RAG\permission-aware-rag"
CREDS_FILE = os.path.join(ROOT_DIR, "artifacts", "test_credential.txt")

with open(CREDS_FILE, "r", encoding="utf-8") as f:
    creds_data = json.load(f)

aws_buckets = [f"ztcyunnn-aws-bucket-{i}" for i in range(1, 6)]
gcp_buckets = [f"ztcyunnn-gcp-bucket-{i}" for i in range(1, 6)]

results = []

for item in creds_data:
    user = item["name"]
    aws_key = item.get("aws_access_key")
    aws_secret = item.get("aws_secret_key")
    gcp_sa = item.get("gcp_credential")

    # AWS Test
    aws_allowed = []
    aws_blocked = []
    if aws_key and aws_secret:
        s3 = boto3.client(
            "s3",
            region_name="ap-south-1",
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
        )
        for b in aws_buckets:
            b_num = b.split("-")[-1]
            try:
                s3.list_objects_v2(Bucket=b, MaxKeys=1)
                aws_allowed.append(f"Bucket-{b_num}")
            except Exception:
                aws_blocked.append(f"Bucket-{b_num}")
    else:
        aws_blocked = [f"Bucket-{b.split('-')[-1]}" for b in aws_buckets]

    # GCP Test
    gcp_allowed = []
    gcp_blocked = []
    if gcp_sa:
        try:
            gcp_credentials = service_account.Credentials.from_service_account_info(gcp_sa)
            storage_client = storage.Client(credentials=gcp_credentials, project=gcp_sa.get("project_id"))
            for b in gcp_buckets:
                b_num = b.split("-")[-1]
                try:
                    bucket = storage_client.bucket(b)
                    # list blobs with max_results=1
                    blobs = list(bucket.list_blobs(max_results=1))
                    gcp_allowed.append(f"Bucket-{b_num}")
                except Exception:
                    gcp_blocked.append(f"Bucket-{b_num}")
        except Exception as e:
            gcp_blocked = [f"Bucket-{b.split('-')[-1]}" for b in gcp_buckets]
    else:
        gcp_blocked = [f"Bucket-{b.split('-')[-1]}" for b in gcp_buckets]

    results.append({
        "user": user,
        "aws_allowed": ", ".join(aws_allowed) if aws_allowed else "NONE",
        "aws_blocked": ", ".join(aws_blocked) if aws_blocked else "NONE",
        "gcp_allowed": ", ".join(gcp_allowed) if gcp_allowed else "NONE",
        "gcp_blocked": ", ".join(gcp_blocked) if gcp_blocked else "NONE",
    })

print("=" * 110)
print(f"  {'User':<16} | {'AWS Allowed Buckets':<25} | {'AWS Blocked':<20} | {'GCP Allowed Buckets':<22} | {'GCP Blocked'}")
print("=" * 110)
for r in results:
    print(f"  {r['user']:<16} | {r['aws_allowed']:<25} | {r['aws_blocked']:<20} | {r['gcp_allowed']:<22} | {r['gcp_blocked']}")
print("=" * 110)
