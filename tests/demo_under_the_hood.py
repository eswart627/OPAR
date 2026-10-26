# tests/demo_under_the_hood.py
# Purpose: Step-by-step interactive walkthrough demonstrating EXACTLY what happens
# under the hood when a user authenticates via Auth0 and accesses multi-cloud RAG documents.

import os
import sys
import json
import base64
import time
import urllib.request
import boto3
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET")
AUTH0_ROLE_ARN = os.environ.get("AUTH0_ROLE_ARN")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
GCP_PROJECT_NUMBER = "283433618318"
GCP_POOL_ID = "auth0-pool"
GCP_PROVIDER_ID = "auth0-provider"
GCP_SA_EMAIL = f"gcp-admin@{GCP_PROJECT_ID}.iam.gserviceaccount.com"

DEFAULT_PASSWORD = "Corporate@Pass2026!"


def decode_jwt_payload(token: str) -> dict:
    """Decodes JWT payload without external libraries."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))


def run_under_the_hood_demo(user_name="user-3"):
    email = f"{user_name}@company.internal"
    print("\n" + "=" * 90)
    print(f"  UNDER THE HOOD DEMO: HOW AUTH0 + AWS STS + GCP WIF WORK FOR [{user_name.upper()}]")
    print("=" * 90)

    # -------------------------------------------------------------
    # STEP 1: Central Corporate Login (Auth0)
    # -------------------------------------------------------------
    print(f"\n[STEP 1] User Logs In to Central Corporate IdP (Auth0)")
    print(f"  * Employee: {email}")
    print(f"  * Action:   POST https://{AUTH0_DOMAIN}/oauth/token (Password Grant)")

    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"
    payload = {
        "grant_type": "http://auth0.com/oauth/grant-type/password-realm",
        "username": email,
        "password": DEFAULT_PASSWORD,
        "realm": "Username-Password-Authentication",
        "client_id": AUTH0_CLIENT_ID,
        "client_secret": AUTH0_CLIENT_SECRET,
        "scope": "openid profile email",
    }
    req = urllib.request.Request(
        token_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=10) as resp:
        auth_data = json.loads(resp.read().decode("utf-8"))
    t_login = (time.perf_counter() - t0) * 1000.0

    id_token = auth_data["id_token"]
    jwt_claims = decode_jwt_payload(id_token)

    print(f"  -> SUCCESS ({t_login:.1f}ms)! Auth0 verified password and issued digitally-signed JWT:")
    print(f"     - Token Issuer (iss): {jwt_claims.get('iss')}")
    print(f"     - Subject ID (sub):   {jwt_claims.get('sub')}")
    print(f"     - Email Claim:        {jwt_claims.get('email')}")
    print(f"     - Audience (aud):     {jwt_claims.get('aud')}")
    print(f"     - Token preview:      {id_token[:40]}...[TRUNCATED]")

    # -------------------------------------------------------------
    # STEP 2: AWS STS Token Exchange (OIDC Federation)
    # -------------------------------------------------------------
    print(f"\n[STEP 2] AWS STS Web Identity Federation")
    print(f"  * Problem: AWS doesn't know user's password. How does AWS grant access?")
    print(f"  * Solution: AWS trusts Auth0! We call sts:AssumeRoleWithWebIdentity")
    print(f"  * Role:     {AUTH0_ROLE_ARN}")

    sts_client = boto3.client("sts", region_name=AWS_REGION)
    t0 = time.perf_counter()
    sts_resp = sts_client.assume_role_with_web_identity(
        RoleArn=AUTH0_ROLE_ARN,
        RoleSessionName=f"eval-session-{user_name}",
        WebIdentityToken=id_token,
        DurationSeconds=3600,
    )
    t_sts = (time.perf_counter() - t0) * 1000.0
    aws_creds = sts_resp["Credentials"]

    print(f"  -> SUCCESS ({t_sts:.1f}ms)! AWS verified Auth0's signature and issued temporary credentials:")
    print(f"     - AccessKeyId:     {aws_creds['AccessKeyId']} (Notice: Starts with ASIA = Temporary STS Session Key!)")
    print(f"     - SecretAccessKey: {aws_creds['SecretAccessKey'][:10]}...[HIDDEN]")
    print(f"     - SessionToken:    {aws_creds['SessionToken'][:35]}...[TRUNCATED]")
    print(f"     - Expires In:      1 hour ({aws_creds['Expiration']})")

    # -------------------------------------------------------------
    # STEP 3: GCP Workload Identity Federation (WIF) Exchange
    # -------------------------------------------------------------
    print(f"\n[STEP 3] Google Cloud Workload Identity Federation")
    print(f"  * Action: Exchange the same Auth0 JWT with Google STS for a federated token")
    print(f"  * Pool:   {GCP_POOL_ID} / Provider: {GCP_PROVIDER_ID}")

    gcp_audience = (
        f"//iam.googleapis.com/projects/{GCP_PROJECT_NUMBER}"
        f"/locations/global/workloadIdentityPools/{GCP_POOL_ID}/providers/{GCP_PROVIDER_ID}"
    )
    sts_url = "https://sts.googleapis.com/v1/token"
    sts_payload = {
        "audience": gcp_audience,
        "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
        "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "subjectTokenType": "urn:ietf:params:oauth:token-type:id_token",
        "subjectToken": id_token,
    }
    gcp_req = urllib.request.Request(
        sts_url,
        data=json.dumps(sts_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(gcp_req, timeout=10) as resp:
        gcp_fed_token = json.loads(resp.read().decode("utf-8"))["access_token"]

    # Impersonate service account
    imp_url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{GCP_SA_EMAIL}:generateAccessToken"
    imp_payload = {"scope": ["https://www.googleapis.com/auth/devstorage.read_only"]}
    imp_req = urllib.request.Request(
        imp_url,
        data=json.dumps(imp_payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {gcp_fed_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(imp_req, timeout=10) as resp:
        gcp_access_token = json.loads(resp.read().decode("utf-8"))["accessToken"]

    print(f"  -> SUCCESS! Google STS verified Auth0 token and minted short-lived OAuth token:")
    print(f"     - GCP Bearer Token: {gcp_access_token[:30]}...[TRUNCATED]")
    print(f"     - Impersonating:    {GCP_SA_EMAIL}")

    # -------------------------------------------------------------
    # STEP 4: How Decoupled SQLite DB Interacts with This User
    # -------------------------------------------------------------
    print(f"\n[STEP 4] The Decoupled SQLite Metadata DB in Action")
    print(f"  * During RAG, FAISS returns document UUIDs without knowing where they live.")
    print(f"  * The system asks SQLite: 'Where are these files located?'")

    db_path = os.path.join(project_root, "artifacts", "metadata.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT global_uuid, provider, bucket, file_name FROM metadata LIMIT 3")
    sample_records = cur.fetchall()
    conn.close()

    print(f"  -> Sample lookup from SQLite artifacts/metadata.db:")
    for r in sample_records:
        uuid_val, prov, bkt, fname = r
        print(f"     [UUID: {uuid_val[:8]}...] -> Provider: {prov:<4} | Bucket: {bkt} | File: {fname}")

    # -------------------------------------------------------------
    # STEP 5: Live Permission Gate Check (AWS S3 using Temporary STS Keys)
    # -------------------------------------------------------------
    print(f"\n[STEP 5] Testing Live Permission Check for [{user_name.upper()}] using ASIA STS Keys")
    s3 = boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=aws_creds["AccessKeyId"],
        aws_secret_access_key=aws_creds["SecretAccessKey"],
        aws_session_token=aws_creds["SessionToken"],
    )

    # Test an allowed bucket vs blocked bucket
    test_buckets = [
        ("ztcyunnn-aws-bucket-3", "doc_id_532.txt", "Should be ALLOWED for user-3"),
        ("ztcyunnn-aws-bucket-1", "doc_id_531.txt", "Should be BLOCKED for user-3"),
    ]

    for bkt, key, desc in test_buckets:
        print(f"  * Checking s3.head_object({bkt}/{key}) ({desc})...")
        try:
            s3.head_object(Bucket=bkt, Key=key)
            print(f"    -> [ACCESS GRANTED]: Document chunk is ALLOWED into LLM context! (Zero Leakage satisfied)")
        except Exception as e:
            err_code = getattr(e, "response", {}).get("Error", {}).get("Code", str(e))
            print(f"    -> [ACCESS DENIED ({err_code})]: Document chunk is FILTERED OUT! LLM will never see it.")

    print("\n" + "=" * 90)
    print(f"  CONCLUSION: ZERO STATIC KEYS USED! {user_name.upper()} ACCESSED S3 USING 1-HOUR TEMPORARY STS KEYS.")
    print("=" * 90 + "\n")


if __name__ == "__main__":
    user = sys.argv[1] if len(sys.argv) > 1 else "user-3"
    run_under_the_hood_demo(user)
