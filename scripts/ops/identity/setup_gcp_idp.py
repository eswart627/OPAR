# scripts/ops/setup_gcp_idp.py
# Purpose: Programmatically provisions GCP Workload Identity Federation (Dynamic Pool & Provider)
# and writes the state blueprint to artifacts/gcp_idp_blueprint.json.

import os
import sys
import json
import time
import datetime
import google.auth
from googleapiclient.discovery import build
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
GCP_PROJECT_NUMBER = os.environ.get("GCP_PROJECT_NUMBER", "283433618318")
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID")

if not GCP_PROJECT_ID or not AUTH0_DOMAIN or not AUTH0_CLIENT_ID:
    raise ValueError("Missing required GCP / Auth0 environment variables in config/.env")

POOL_ID = "auth0-pool-dynamic"
PROVIDER_ID = "auth0-provider-dynamic"
SA_EMAIL = f"gcp-admin@{GCP_PROJECT_ID}.iam.gserviceaccount.com"
BLUEPRINT_PATH = os.path.join(project_root, "artifacts", "gcp_idp_blueprint.json")


def wait_for_operation(iam, op_name, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if "/providers/" in op_name:
            op = iam.projects().locations().workloadIdentityPools().providers().operations().get(name=op_name).execute()
        else:
            op = iam.projects().locations().workloadIdentityPools().operations().get(name=op_name).execute()
        if op.get("done"):
            if "error" in op:
                raise RuntimeError(f"Operation failed: {op['error']}")
            return op.get("response")
        time.sleep(1.5)
    raise TimeoutError(f"Operation {op_name} timed out after {timeout}s")


def setup_gcp_idp():
    print("=" * 80)
    print("  GCP WORKLOAD IDENTITY FEDERATION SETUP (DYNAMIC BLUEPRINT)")
    print("=" * 80)

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    iam = build("iam", "v1", credentials=creds)

    parent_location = f"projects/{GCP_PROJECT_NUMBER}/locations/global"
    pool_resource = f"{parent_location}/workloadIdentityPools/{POOL_ID}"
    provider_resource = f"{pool_resource}/providers/{PROVIDER_ID}"

    # 1. Check or Create Workload Identity Pool
    try:
        iam.projects().locations().workloadIdentityPools().get(name=pool_resource).execute()
        print(f"  [GCP] Found Existing Workload Identity Pool: {POOL_ID}")
    except Exception:
        print(f"  [GCP] Creating Workload Identity Pool: {POOL_ID}...")
        pool_body = {
            "displayName": POOL_ID,
            "disabled": False,
            "description": "Dynamic Auth0 Workload Identity Pool created via setup_gcp_idp.py"
        }
        op = iam.projects().locations().workloadIdentityPools().create(
            parent=parent_location,
            workloadIdentityPoolId=POOL_ID,
            body=pool_body
        ).execute()
        wait_for_operation(iam, op["name"])
        print(f"  [GCP] Created Workload Identity Pool: {pool_resource}")

    # 2. Check or Create OIDC Provider
    try:
        iam.projects().locations().workloadIdentityPools().providers().get(name=provider_resource).execute()
        print(f"  [GCP] Found Existing Provider: {PROVIDER_ID}")
    except Exception:
        print(f"  [GCP] Creating OIDC Provider: {PROVIDER_ID}...")
        provider_body = {
            "displayName": PROVIDER_ID,
            "disabled": False,
            "attributeMapping": {
                "google.subject": "assertion.sub",
                "attribute.email": "assertion.email"
            },
            "oidc": {
                "issuerUri": f"https://{AUTH0_DOMAIN}/",
                "allowedAudiences": [AUTH0_CLIENT_ID]
            }
        }
        op = iam.projects().locations().workloadIdentityPools().providers().create(
            parent=pool_resource,
            workloadIdentityPoolProviderId=PROVIDER_ID,
            body=provider_body
        ).execute()
        wait_for_operation(iam, op["name"])
        print(f"  [GCP] Created OIDC Provider: {provider_resource}")

    # 3. Bind Service Account to Dynamic Pool
    member_identity = f"principalSet://iam.googleapis.com/projects/{GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/{POOL_ID}/*"
    sa_resource = f"projects/{GCP_PROJECT_ID}/serviceAccounts/{SA_EMAIL}"
    role_key = "roles/iam.workloadIdentityUser"

    print(f"  [GCP] Authorizing {member_identity} on {SA_EMAIL}...")
    sa_policy = iam.projects().serviceAccounts().getIamPolicy(resource=sa_resource).execute()

    binding_found = False
    for b in sa_policy.get("bindings", []):
        if b["role"] == role_key:
            if member_identity not in b["members"]:
                b["members"].append(member_identity)
            binding_found = True
            break

    if not binding_found:
        sa_policy.setdefault("bindings", []).append({
            "role": role_key,
            "members": [member_identity]
        })

    iam.projects().serviceAccounts().setIamPolicy(
        resource=sa_resource,
        body={"policy": sa_policy}
    ).execute()
    print(f"  [GCP] IAM policy updated successfully for {SA_EMAIL}")

    # 4. Save Blueprint State to JSON
    blueprint = {
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "project_id": GCP_PROJECT_ID,
        "project_number": GCP_PROJECT_NUMBER,
        "pool_id": POOL_ID,
        "pool_resource": pool_resource,
        "provider_id": PROVIDER_ID,
        "provider_resource": provider_resource,
        "issuer_url": f"https://{AUTH0_DOMAIN}/",
        "allowed_audience": AUTH0_CLIENT_ID,
        "service_account": SA_EMAIL,
        "member_identity": member_identity,
    }

    os.makedirs(os.path.dirname(BLUEPRINT_PATH), exist_ok=True)
    with open(BLUEPRINT_PATH, "w", encoding="utf-8") as f:
        json.dump(blueprint, f, indent=2)

    print(f"  [GCP] Dynamic Blueprint saved to: {BLUEPRINT_PATH}")
    print("=" * 80)
    print("  >>> GCP IDP PROVISIONING COMPLETE")
    print("=" * 80 + "\n")
    return blueprint


if __name__ == "__main__":
    setup_gcp_idp()
