# scripts/ops/utils/audit_cloud_resources.py
# Purpose: Real-time inspection and auditing of multi-cloud resources across Auth0, AWS, and GCP.

import os
import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

print("=" * 80)
print("  MULTI-CLOUD & IDP RESOURCE AUDIT")
print("=" * 80)

# ---------------------------------------------------------------------
# 1. Auth0 Audit
# ---------------------------------------------------------------------
print("\n>>> [1/3] AUDITING AUTH0 TENANT...")
auth0_domain = os.environ.get("AUTH0_DOMAIN")
auth0_cid = os.environ.get("AUTH0_CLIENT_ID")
auth0_csec = os.environ.get("AUTH0_CLIENT_SECRET")

try:
    token_url = f"https://{auth0_domain}/oauth/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": auth0_cid,
        "client_secret": auth0_csec,
        "audience": f"https://{auth0_domain}/api/v2/",
    }
    req = urllib.request.Request(
        token_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        mgmt_token = json.loads(resp.read().decode("utf-8"))["access_token"]

    users_url = f"https://{auth0_domain}/api/v2/users"
    req_users = urllib.request.Request(
        users_url,
        headers={"Authorization": f"Bearer {mgmt_token}"},
        method="GET",
    )
    with urllib.request.urlopen(req_users, timeout=10) as resp:
        users = json.loads(resp.read().decode("utf-8"))

    company_users = [u for u in users if u.get("email", "").endswith("@company.internal")]
    print(f"  Auth0 Total Users: {len(users)}")
    print(f"  Auth0 '@company.internal' Users: {len(company_users)}")
    for cu in company_users:
        print(f"    - {cu.get('email')} (ID: {cu.get('user_id')})")
except Exception as e:
    print(f"  [ERROR] Auth0 audit failed: {e}")

# ---------------------------------------------------------------------
# 2. AWS Audit
# ---------------------------------------------------------------------
print("\n>>> [2/3] AUDITING AWS RESOURCES...")
aws_region = os.environ.get("AWS_REGION", "ap-south-1")
aws_key = os.environ.get("AWS_ADMIN_ACCESS_KEY")
aws_sec = os.environ.get("AWS_ADMIN_SECRET_KEY")

try:
    import boto3
    s3 = boto3.client("s3", region_name=aws_region, aws_access_key_id=aws_key, aws_secret_access_key=aws_sec)
    sqs = boto3.client("sqs", region_name=aws_region, aws_access_key_id=aws_key, aws_secret_access_key=aws_sec)
    iam = boto3.client("iam", region_name=aws_region, aws_access_key_id=aws_key, aws_secret_access_key=aws_sec)

    # S3 Buckets
    buckets = s3.list_buckets().get("Buckets", [])
    rag_buckets = [b["Name"] for b in buckets if "-aws-bucket-" in b["Name"] or "ztcyunnn" in b["Name"]]
    print(f"  AWS Project S3 Buckets: {len(rag_buckets)}")
    for b in rag_buckets:
        print(f"    - Bucket: {b}")

    # SQS Queues
    try:
        queues = sqs.list_queues().get("QueueUrls", [])
        rag_queues = [q for q in queues if "drift" in q or "permission" in q]
    except Exception:
        rag_queues = []
    print(f"  AWS Drift SQS Queues: {len(rag_queues)}")
    for q in rag_queues:
        print(f"    - SQS Queue: {q}")

    # IAM OIDC Providers
    oidc_providers = iam.list_open_id_connect_providers().get("OpenIDConnectProviderList", [])
    auth0_oidc = [p["Arn"] for p in oidc_providers if auth0_domain and auth0_domain in p["Arn"]]
    print(f"  AWS OIDC IdP Providers for Auth0: {len(auth0_oidc)}")
    for p in auth0_oidc:
        print(f"    - OIDC Provider: {p}")

    # IAM Roles for Auth0
    auth0_roles = []
    for r in iam.list_roles().get("Roles", []):
        rname = r["RoleName"]
        if "Auth0" in rname or "Corporate-RAG" in rname:
            auth0_roles.append(rname)
    print(f"  AWS IAM Roles for Auth0: {len(auth0_roles)}")
    for r in auth0_roles:
        print(f"    - IAM Role: {r}")

    # IAM Users
    users_aws = [u["UserName"] for u in iam.list_users().get("Users", []) if u["UserName"].startswith("user-") or u["UserName"] == "baseline-admin"]
    print(f"  AWS Project IAM Users: {len(users_aws)}")
    for u in users_aws:
        print(f"    - IAM User: {u}")
except Exception as e:
    print(f"  [ERROR] AWS audit failed: {e}")

# ---------------------------------------------------------------------
# 3. GCP Audit
# ---------------------------------------------------------------------
print("\n>>> [3/3] AUDITING GCP RESOURCES...")
gcp_project_id = os.environ.get("GCP_PROJECT_ID", "project-444c1d0a-6028-4d76-8d5")
gcp_key = os.path.join(project_root, os.environ.get("GCP_ADMIN_KEY_PATH", ""))

try:
    from google.cloud import storage, pubsub_v1
    from googleapiclient.discovery import build
    from google.oauth2 import service_account

    if os.path.exists(gcp_key):
        storage_client = storage.Client.from_service_account_json(gcp_key)
        creds = service_account.Credentials.from_service_account_file(
            gcp_key, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        iam_admin = build("iam", "v1", credentials=creds)

        # GCS Buckets
        all_buckets = list(storage_client.list_buckets())
        gcp_rag_buckets = [b.name for b in all_buckets if "-gcp-bucket-" in b.name or "ztcyunnn" in b.name]
        print(f"  GCP Project Storage Buckets: {len(gcp_rag_buckets)}")
        for b in gcp_rag_buckets:
            print(f"    - GCS Bucket: {b}")

        # PubSub Topics & Subscriptions
        pub_client = pubsub_v1.PublisherClient.from_service_account_json(gcp_key)
        sub_client = pubsub_v1.SubscriberClient.from_service_account_json(gcp_key)
        
        topics = [t for t in pub_client.list_topics(project=f"projects/{gcp_project_id}")]
        rag_topics = [t.name for t in topics if "drift" in t.name or "permission" in t.name]
        print(f"  GCP Pub/Sub Topics: {len(rag_topics)}")
        for t in rag_topics:
            print(f"    - Topic: {t}")

        subs = [s for s in sub_client.list_subscriptions(project=f"projects/{gcp_project_id}")]
        rag_subs = [s.name for s in subs if "drift" in s.name or "permission" in s.name]
        print(f"  GCP Pub/Sub Subscriptions: {len(rag_subs)}")
        for s in rag_subs:
            print(f"    - Subscription: {s}")

        # Service Accounts
        sa_res = iam_admin.projects().serviceAccounts().list(name=f"projects/{gcp_project_id}").execute()
        sa_list = [sa["email"] for sa in sa_res.get("accounts", []) if any(sa["email"].startswith(f"user-{i}") or "baseline-admin" in sa["email"] for i in range(1, 8))]
        print(f"  GCP Project Service Accounts: {len(sa_list)}")
        for sa in sa_list:
            print(f"    - Service Account: {sa}")

        # Workload Identity Pools & Providers
        parent = f"projects/{gcp_project_id}/locations/global"
        try:
            pools = iam_admin.projects().locations().workloadIdentityPools().list(parent=parent).execute().get("workloadIdentityPools", [])
            rag_pools = [p["name"] for p in pools if "auth0" in p["name"]]
        except Exception as e:
            rag_pools = [f"Pool check note: {e}"]
        print(f"  GCP Workload Identity Pools: {len(rag_pools)}")
        for p in rag_pools:
            print(f"    - WIF Pool: {p}")
except Exception as e:
    print(f"  [ERROR] GCP audit failed: {e}")

print("\n" + "=" * 80)
print("  AUDIT COMPLETED")
print("=" * 80)

if __name__ == "__main__":
    pass
