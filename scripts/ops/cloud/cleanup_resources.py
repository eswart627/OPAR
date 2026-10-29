# scripts/cleanup_resources.py
# Purpose: Clean up / deprovision all GCP and AWS resources created by the project.

import os
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
env_path = os.path.join(project_root, "config", ".env")
load_dotenv(dotenv_path=env_path, override=True)
if not os.path.exists(env_path):
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=True)

sys.stdout.reconfigure(line_buffering=True)

ARTIFACTS_DIR = Path(project_root) / "artifacts"
ACCESS_MODEL_PATH = ARTIFACTS_DIR / "generated_access_model.json"

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "project-444c1d0a-6028-4d76-8d5")
GCP_ADMIN_KEY_PATH = os.environ.get("GCP_ADMIN_KEY_PATH", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ADMIN_ACCESS_KEY = os.environ.get("AWS_ADMIN_ACCESS_KEY", "")
AWS_ADMIN_SECRET_KEY = os.environ.get("AWS_ADMIN_SECRET_KEY", "")

def load_model():
    if not os.path.exists(ACCESS_MODEL_PATH):
        print(f"[WARN] Access model not found at {ACCESS_MODEL_PATH}")
        return None
    with open(ACCESS_MODEL_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# ---------------------------------------------------------------------
# GCP Cleanup
# ---------------------------------------------------------------------
def cleanup_gcp(model=None):
    print("\n" + "="*50)
    print(">>> Starting GCP Cleanup...")
    print("="*50)

    try:
        from google.cloud import storage
        from googleapiclient.discovery import build
        import google.auth
    except ImportError:
        print("[GCP][ERROR] google-cloud-storage or googleapiclient not installed.")
        return

    try:
        resolved_key = GCP_ADMIN_KEY_PATH
        if resolved_key and not os.path.isabs(resolved_key):
            resolved_key = os.path.abspath(os.path.join(project_root, resolved_key))

        if resolved_key and os.path.exists(resolved_key):
            storage_client = storage.Client.from_service_account_json(resolved_key)
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(
                resolved_key, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            iam_client = build("iam", "v1", credentials=creds)
        else:
            storage_client = storage.Client(project=GCP_PROJECT_ID)
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            iam_client = build("iam", "v1", credentials=creds)
    except Exception as e:
        print(f"[GCP][ERROR] Failed to authenticate GCP client: {e}")
        return

    # 1. Delete GCP Buckets
    if model and "GCP_RBAC" in model:
        buckets_to_delete = list(set(model["GCP_RBAC"].values()))
    else:
        # Search for any project buckets with -gcp-bucket- pattern
        buckets_to_delete = [b.name for b in storage_client.list_buckets() if "-gcp-bucket-" in b.name]

    for bucket_name in buckets_to_delete:
        try:
            bucket = storage_client.get_bucket(bucket_name)
            # Empty all blobs first
            blobs = list(bucket.list_blobs())
            for blob in blobs:
                blob.delete()
            bucket.delete(force=True)
            print(f"[GCP][INFO] Deleted bucket: {bucket_name} (and {len(blobs)} files)")
        except Exception as e:
            if "404" in str(e) or "Not Found" in str(e):
                print(f"[GCP][INFO] Bucket already gone: {bucket_name}")
            else:
                print(f"[GCP][WARN] Could not delete bucket {bucket_name}: {e}")

    # 2. Delete GCP Service Accounts
    users = model.get("USERS", []) if model else [
        {"name": f"user-{i}"} for i in range(1, 8)
    ] + [{"name": "baseline-admin"}]

    for u in users:
        sa_id = u["name"].lower()
        sa_email = f"{sa_id}@{GCP_PROJECT_ID}.iam.gserviceaccount.com"
        sa_resource = f"projects/{GCP_PROJECT_ID}/serviceAccounts/{sa_email}"
        try:
            iam_client.projects().serviceAccounts().delete(name=sa_resource).execute()
            print(f"[GCP][INFO] Deleted service account: {sa_email}")
        except Exception as e:
            if "404" in str(e) or "Not Found" in str(e):
                print(f"[GCP][INFO] Service account already gone: {sa_email}")
            else:
                print(f"[GCP][WARN] Could not delete service account {sa_email}: {e}")

    try:
        from scripts.ops.drift.deprovision_gcp_drift_queue import deprovision_gcp_drift_queue
        deprovision_gcp_drift_queue()
    except Exception as e:
        print(f"[GCP][WARN] Could not deprovision drift queue: {e}")

    print("[GCP][SUCCESS] GCP cleanup completed successfully!")

# ---------------------------------------------------------------------
# AWS Cleanup
# ---------------------------------------------------------------------
def cleanup_aws(model=None):
    print("\n" + "="*50)
    print(">>> Starting AWS Cleanup...")
    print("="*50)

    if not (AWS_ADMIN_ACCESS_KEY and AWS_ADMIN_SECRET_KEY):
        print("[AWS][INFO] No AWS credentials found in .env; skipping AWS cleanup.")
        return

    import boto3

    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=AWS_ADMIN_ACCESS_KEY,
            aws_secret_access_key=AWS_ADMIN_SECRET_KEY,
            region_name=AWS_REGION,
        )
        iam = boto3.client(
            "iam",
            aws_access_key_id=AWS_ADMIN_ACCESS_KEY,
            aws_secret_access_key=AWS_ADMIN_SECRET_KEY,
            region_name=AWS_REGION,
        )
        account_id = boto3.client(
            "sts",
            aws_access_key_id=AWS_ADMIN_ACCESS_KEY,
            aws_secret_access_key=AWS_ADMIN_SECRET_KEY,
            region_name=AWS_REGION,
        ).get_caller_identity()["Account"]
    except Exception as e:
        print(f"[AWS][ERROR] Failed to initialize AWS clients: {e}")
        return

    # 1. Delete AWS S3 Buckets
    if model and "AWS_ABAC" in model:
        aws_buckets = list(model["AWS_ABAC"].keys())
    else:
        # Region-based fallback: find all buckets matching '-aws-bucket-' in AWS_REGION
        aws_buckets = []
        try:
            for b in s3.list_buckets().get("Buckets", []):
                name = b["Name"]
                if "-aws-bucket-" in name:
                    try:
                        loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
                        if loc == AWS_REGION or (loc is None and AWS_REGION in ("us-east-1", "")):
                            aws_buckets.append(name)
                        elif not loc:
                            aws_buckets.append(name)
                    except Exception:
                        aws_buckets.append(name)
        except Exception as e:
            print(f"[AWS][WARN] Could not list S3 buckets: {e}")

    for bucket_name in aws_buckets:
        try:
            # Delete all objects in bucket
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket_name):
                if "Contents" in page:
                    objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                    s3.delete_objects(Bucket=bucket_name, Delete={"Objects": objects})
            s3.delete_bucket(Bucket=bucket_name)
            print(f"[AWS][INFO] Deleted S3 bucket: {bucket_name}")
        except Exception as e:
            if "NoSuchBucket" in str(e):
                print(f"[AWS][INFO] Bucket already gone: {bucket_name}")
            else:
                print(f"[AWS][WARN] Could not delete bucket {bucket_name}: {e}")

    # 2. Clean up Users (Access keys, attached policies, group memberships)
    users = model.get("USERS", []) if model else [
        {"name": f"user-{i}"} for i in range(1, 8)
    ] + [{"name": "baseline-admin"}]

    for u in users:
        uname = u["name"]
        # Delete access keys
        try:
            keys = iam.list_access_keys(UserName=uname).get("AccessKeyMetadata", [])
            for k in keys:
                iam.delete_access_key(UserName=uname, AccessKeyId=k["AccessKeyId"])
        except Exception:
            pass

        # Detach user policies
        try:
            pols = iam.list_attached_user_policies(UserName=uname).get("AttachedPolicies", [])
            for p in pols:
                iam.detach_user_policy(UserName=uname, PolicyArn=p["PolicyArn"])
        except Exception:
            pass

        # Remove from groups
        try:
            grps = iam.list_groups_for_user(UserName=uname).get("Groups", [])
            for g in grps:
                iam.remove_user_from_group(GroupName=g["GroupName"], UserName=uname)
        except Exception:
            pass

        # Delete user
        try:
            iam.delete_user(UserName=uname)
            print(f"[AWS][INFO] Deleted IAM user: {uname}")
        except Exception as e:
            if "NoSuchEntity" in str(e):
                print(f"[AWS][INFO] IAM user already gone: {uname}")
            else:
                print(f"[AWS][WARN] Could not delete user {uname}: {e}")

    # 3. Clean up Groups and Policies
    if model and "USER_GROUPS" in model:
        user_groups_list = list(model["USER_GROUPS"].keys())
    else:
        try:
            user_groups_list = [g["GroupName"] for g in iam.list_groups().get("Groups", []) if g["GroupName"].startswith("g-") or "abac" in g["GroupName"]]
        except Exception:
            user_groups_list = []

    for gname in user_groups_list:
        # Detach attached policies from group
        try:
            gpols = iam.list_attached_group_policies(GroupName=gname).get("AttachedPolicies", [])
            for p in gpols:
                iam.detach_group_policy(GroupName=gname, PolicyArn=p["PolicyArn"])
            iam.delete_group(GroupName=gname)
            print(f"[AWS][INFO] Deleted IAM group: {gname}")
        except Exception as e:
            if "NoSuchEntity" not in str(e):
                print(f"[AWS][WARN] Could not delete group {gname}: {e}")

    # Delete custom ABAC policies
    for bucket_name in aws_buckets:
        policy_name = f"abac-{bucket_name}-policy"
        policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
        try:
            iam.delete_policy(PolicyArn=policy_arn)
            print(f"[AWS][INFO] Deleted IAM policy: {policy_name}")
        except Exception as e:
            if "NoSuchEntity" not in str(e):
                print(f"[AWS][WARN] Could not delete policy {policy_name}: {e}")

    try:
        from scripts.ops.drift.deprovision_aws_drift_queue import deprovision_aws_drift_queue
        deprovision_aws_drift_queue()
    except Exception as e:
        print(f"[AWS][WARN] Could not deprovision drift queue: {e}")

    print("[AWS][SUCCESS] AWS cleanup completed successfully!")

if __name__ == "__main__":
    model = load_model()
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if target in ("all", "gcp"):
        cleanup_gcp(model)
    if target in ("all", "aws"):
        cleanup_aws(model)

    # Delete access model JSON at the very end after deprovisioning
    if target == "all" and os.path.exists(ACCESS_MODEL_PATH):
        try:
            os.remove(ACCESS_MODEL_PATH)
            print(f"[CLEANUP][INFO] Deleted {ACCESS_MODEL_PATH}")
        except Exception as e:
            print(f"[CLEANUP][WARN] Could not delete {ACCESS_MODEL_PATH}: {e}")
