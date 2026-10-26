# tests/test_dynamic_idp_matrix.py
# Purpose: Validates that the dynamically provisioned AWS & GCP IDPs work end-to-end
# with Auth0 users, minting live STS tokens from both clouds.

import os
import sys
import json
import time
import urllib.request
import urllib.error
import boto3
from google.cloud import storage
from google.oauth2 import credentials as g_credentials
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

AWS_BLUEPRINT = os.path.join(project_root, "artifacts", "aws_idp_blueprint.json")
GCP_BLUEPRINT = os.path.join(project_root, "artifacts", "gcp_idp_blueprint.json")

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET")
DEFAULT_PASSWORD = "Corporate@Pass2026!"

if not AUTH0_DOMAIN or not AUTH0_CLIENT_ID or not AUTH0_CLIENT_SECRET:
    raise ValueError("Missing required Auth0 environment variables in config/.env")


def get_auth0_token(email: str) -> str:
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
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))["id_token"]


def test_dynamic_idp_federation():
    print("=" * 85)
    print("  TESTING DYNAMIC MULTI-CLOUD IDENTITY FEDERATION")
    print("=" * 85)

    if not os.path.exists(AWS_BLUEPRINT):
        raise FileNotFoundError(f"AWS blueprint missing: {AWS_BLUEPRINT}")
    if not os.path.exists(GCP_BLUEPRINT):
        raise FileNotFoundError(f"GCP blueprint missing: {GCP_BLUEPRINT}")

    with open(AWS_BLUEPRINT, "r", encoding="utf-8") as f:
        aws_bp = json.load(f)
    with open(GCP_BLUEPRINT, "r", encoding="utf-8") as f:
        gcp_bp = json.load(f)

    test_user = "baseline-admin@company.internal"
    print(f"\n[1] Authenticating {test_user} via Auth0...")
    t0 = time.perf_counter()
    id_token = get_auth0_token(test_user)
    t_auth0 = (time.perf_counter() - t0) * 1000.0
    print(f"    -> Success! Auth0 ID Token acquired in {t_auth0:.1f}ms")

    # AWS STS with Dynamic Role
    role_arn = aws_bp["role_arn"]
    print(f"\n[2] Exchanging Token with AWS STS for Dynamic Role: {role_arn}...")
    sts_client = boto3.client("sts", region_name=aws_bp["region"])
    sts_resp = sts_client.assume_role_with_web_identity(
        RoleArn=role_arn,
        RoleSessionName="dynamic-test-session",
        WebIdentityToken=id_token,
        DurationSeconds=3600,
    )
    aws_creds = sts_resp["Credentials"]
    print(f"    -> Success! Minted AWS STS Session Key: {aws_creds['AccessKeyId']}")

    # Verify AWS S3 List
    s3 = boto3.client(
        "s3",
        region_name=aws_bp["region"],
        aws_access_key_id=aws_creds["AccessKeyId"],
        aws_secret_access_key=aws_creds["SecretAccessKey"],
        aws_session_token=aws_creds["SessionToken"],
    )
    aws_buckets = s3.list_buckets().get("Buckets", [])
    print(f"    -> Live AWS S3 API Call Succeeded! Accessible buckets visible: {len(aws_buckets)}")

    # GCP STS with Dynamic Pool & Provider
    print(f"\n[3] Exchanging Token with GCP STS for Dynamic Pool: {gcp_bp['pool_id']}...")
    gcp_audience = (
        f"//iam.googleapis.com/projects/{gcp_bp['project_number']}"
        f"/locations/global/workloadIdentityPools/{gcp_bp['pool_id']}/providers/{gcp_bp['provider_id']}"
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
    req = urllib.request.Request(
        sts_url,
        data=json.dumps(sts_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        federated_token = json.loads(resp.read().decode("utf-8"))["access_token"]
    print(f"    -> Success! Minted GCP Federated Token from Dynamic Pool.")

    # Impersonate service account
    sa_email = gcp_bp["service_account"]
    imp_url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:generateAccessToken"
    imp_payload = {"scope": ["https://www.googleapis.com/auth/devstorage.read_only"]}
    imp_req = urllib.request.Request(
        imp_url,
        data=json.dumps(imp_payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {federated_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(imp_req, timeout=10) as resp:
            gcp_access_token = json.loads(resp.read().decode("utf-8"))["accessToken"]
        print(f"    -> Success! Impersonated {sa_email} with Dynamic Workload Identity.")
    except urllib.error.HTTPError as e:
        print(f"    [GCP Impersonate Error {e.code}]: {e.read().decode('utf-8')}")
        raise

    # Verify GCP Storage List
    g_creds = g_credentials.Credentials(token=gcp_access_token)
    storage_client = storage.Client(credentials=g_creds, project=gcp_bp["project_id"])
    gcp_bucket_list = list(storage_client.list_buckets(max_results=5))
    print(f"    -> Live GCP Storage API Call Succeeded! Buckets visible: {len(gcp_bucket_list)}")

    print("\n" + "=" * 85)
    print("  >>> DYNAMIC MULTI-CLOUD IDP VERIFICATION PASSED 100%!")
    print("=" * 85 + "\n")


if __name__ == "__main__":
    test_dynamic_idp_federation()
