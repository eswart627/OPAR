# scripts/ops/deprovision_gcp_idp.py
# Purpose: Reads artifacts/gcp_idp_blueprint.json and safely tears down
# ONLY the dynamic Workload Identity Pool, Provider, and IAM bindings, leaving manual resources untouched.

import os
import sys
import json
import time
import google.auth
from googleapiclient.discovery import build
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

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


def deprovision_gcp_idp():
    print("=" * 80)
    print("  GCP WORKLOAD IDENTITY FEDERATION DEPROVISIONING")
    print("=" * 80)

    if not os.path.exists(BLUEPRINT_PATH):
        print(f"  [GCP][INFO] No blueprint found at {BLUEPRINT_PATH}. Nothing to deprovision.")
        return True

    with open(BLUEPRINT_PATH, "r", encoding="utf-8") as f:
        blueprint = json.load(f)

    project_id = blueprint.get("project_id")
    sa_email = blueprint.get("service_account")
    member_identity = blueprint.get("member_identity")
    provider_resource = blueprint.get("provider_resource")
    pool_resource = blueprint.get("pool_resource")

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    iam = build("iam", "v1", credentials=creds)

    # 1. Remove Service Account IAM Binding
    if sa_email and member_identity:
        sa_resource = f"projects/{project_id}/serviceAccounts/{sa_email}"
        role_key = "roles/iam.workloadIdentityUser"
        try:
            policy = iam.projects().serviceAccounts().getIamPolicy(resource=sa_resource).execute()
            changed = False
            for b in policy.get("bindings", []):
                if b["role"] == role_key and member_identity in b["members"]:
                    b["members"].remove(member_identity)
                    changed = True
                    break
            if changed:
                iam.projects().serviceAccounts().setIamPolicy(
                    resource=sa_resource,
                    body={"policy": policy}
                ).execute()
                print(f"  [GCP] Removed IAM binding for {member_identity} on {sa_email}")
            else:
                print(f"  [GCP] Member {member_identity} was not found in IAM policy.")
        except Exception as e:
            print(f"  [GCP][WARN] Error updating IAM policy for {sa_email}: {e}")

    # 2. Delete Provider
    if provider_resource:
        try:
            print(f"  [GCP] Deleting Provider: {provider_resource}...")
            op = iam.projects().locations().workloadIdentityPools().providers().delete(
                name=provider_resource
            ).execute()
            wait_for_operation(iam, op["name"])
            print(f"  [GCP] Deleted Provider: {provider_resource}")
        except Exception as e:
            print(f"  [GCP][WARN] Could not delete provider: {e}")

    # 3. Delete Pool
    if pool_resource:
        try:
            print(f"  [GCP] Deleting Pool: {pool_resource}...")
            op = iam.projects().locations().workloadIdentityPools().delete(
                name=pool_resource
            ).execute()
            wait_for_operation(iam, op["name"])
            print(f"  [GCP] Deleted Pool: {pool_resource}")
        except Exception as e:
            print(f"  [GCP][WARN] Could not delete pool: {e}")

    # 4. Remove Blueprint File
    try:
        os.remove(BLUEPRINT_PATH)
        print(f"  [GCP] Removed Blueprint file: {BLUEPRINT_PATH}")
    except Exception as e:
        print(f"  [GCP][WARN] Could not remove blueprint file: {e}")

    print("=" * 80)
    print("  >>> GCP DYNAMIC IDP DEPROVISIONING COMPLETE (Manual pool was untouched)")
    print("=" * 80 + "\n")
    return True


if __name__ == "__main__":
    deprovision_gcp_idp()
