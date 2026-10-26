# scripts/ops/deprovision_aws_idp.py
# Purpose: Reads artifacts/aws_idp_blueprint.json and safely tears down
# the dynamic IAM role, policy bindings, and OIDC provider.

import os
import sys
import json
import boto3
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ACCESS_KEY = os.environ.get("AWS_ADMIN_ACCESS_KEY")
AWS_SECRET_KEY = os.environ.get("AWS_ADMIN_SECRET_KEY")

BLUEPRINT_PATH = os.path.join(project_root, "artifacts", "aws_idp_blueprint.json")


def deprovision_aws_idp():
    print("=" * 80)
    print("  AWS OIDC IDENTITY FEDERATION DEPROVISIONING")
    print("=" * 80)

    if not os.path.exists(BLUEPRINT_PATH):
        print(f"  [AWS][INFO] No blueprint found at {BLUEPRINT_PATH}. Checking by role name...")
        role_name = "Corporate-RAG-Auth0-Role"
        policy_arn = "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
        oidc_provider_arn = None
    else:
        with open(BLUEPRINT_PATH, "r", encoding="utf-8") as f:
            blueprint = json.load(f)
        role_name = blueprint.get("role_name", "Corporate-RAG-Auth0-Role")
        policy_arn = blueprint.get("attached_policy_arn", "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess")
        oidc_provider_arn = blueprint.get("oidc_provider_arn")

    iam = boto3.client(
        "iam",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )

    # 1. Detach Policy
    if role_name and policy_arn:
        try:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            print(f"  [AWS] Detached policy {policy_arn} from role {role_name}")
        except Exception as e:
            print(f"  [AWS][WARN] Could not detach policy {policy_arn}: {e}")

    # 2. Delete Role
    if role_name:
        try:
            iam.delete_role(RoleName=role_name)
            print(f"  [AWS][SUCCESS] Deleted IAM Role: {role_name}")
        except Exception as e:
            print(f"  [AWS][WARN] Could not delete role {role_name}: {e}")

    # 3. Delete OIDC Provider
    auth0_domain = os.environ.get("AUTH0_DOMAIN", "dev-26pdgc8nkquxcu0n.us.auth0.com")
    try:
        providers = iam.list_open_id_connect_providers().get("OpenIDConnectProviderList", [])
        for p in providers:
            arn = p["Arn"]
            if auth0_domain in arn or (oidc_provider_arn and oidc_provider_arn == arn):
                iam.delete_open_id_connect_provider(OpenIDConnectProviderArn=arn)
                print(f"  [AWS][SUCCESS] Deleted OIDC Provider: {arn}")
    except Exception as e:
        print(f"  [AWS][WARN] Could not delete OIDC provider: {e}")

    # 4. Remove Blueprint File
    if os.path.exists(BLUEPRINT_PATH):
        try:
            os.remove(BLUEPRINT_PATH)
            print(f"  [AWS] Removed Blueprint file: {BLUEPRINT_PATH}")
        except Exception as e:
            print(f"  [AWS][WARN] Could not remove blueprint file: {e}")

    print("=" * 80)
    print("  >>> AWS IDP DEPROVISIONING COMPLETE")
    print("=" * 80 + "\n")
    return True


if __name__ == "__main__":
    deprovision_aws_idp()
