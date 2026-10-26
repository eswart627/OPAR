# scripts/ops/setup_aws_idp.py
# Purpose: Programmatically provisions AWS OIDC Identity Provider Federation (Dynamic Role)
# and writes the state blueprint to artifacts/aws_idp_blueprint.json.

import os
import sys
import json
import datetime
import boto3
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ACCESS_KEY = os.environ.get("AWS_ADMIN_ACCESS_KEY")
AWS_SECRET_KEY = os.environ.get("AWS_ADMIN_SECRET_KEY")

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID")

if not AUTH0_DOMAIN or not AUTH0_CLIENT_ID:
    raise ValueError("Missing required Auth0 environment variables (AUTH0_DOMAIN, AUTH0_CLIENT_ID) in config/.env")

ROLE_NAME = "Corporate-RAG-Auth0-Role"
POLICY_ARN = "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
BLUEPRINT_PATH = os.path.join(project_root, "artifacts", "aws_idp_blueprint.json")


def setup_aws_idp():
    print("=" * 80)
    print("  AWS OIDC IDENTITY FEDERATION SETUP (DYNAMIC BLUEPRINT)")
    print("=" * 80)

    iam = boto3.client(
        "iam",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )
    sts = boto3.client(
        "sts",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )

    account_id = sts.get_caller_identity()["Account"]
    print(f"  [AWS] Authenticated AWS Account ID: {account_id}")

    # 1. Resolve or Create OIDC Provider
    target_provider_url = f"{AUTH0_DOMAIN}/"
    expected_arn = f"arn:aws:iam::{account_id}:oidc-provider/{target_provider_url}"

    existing_providers = iam.list_open_id_connect_providers()["OpenIDConnectProviderList"]
    existing_arns = [p["Arn"] for p in existing_providers]

    oidc_provider_arn = None
    for arn in existing_arns:
        if target_provider_url in arn:
            oidc_provider_arn = arn
            break

    if not oidc_provider_arn:
        print(f"  [AWS] Registering new OIDC Provider for: https://{target_provider_url}")
        # Top-level CA + Leaf cert thumbprints for Auth0
        thumbprints = [
            "6938fd4d98bab03faadb97b34396831e3780aea1",
            "00c5d1fb0d58163b5f5bc0bf03d4afedd77f8507",
        ]
        resp = iam.create_open_id_connect_provider(
            Url=f"https://{target_provider_url}",
            ClientIDList=[AUTH0_CLIENT_ID],
            ThumbprintList=thumbprints,
        )
        oidc_provider_arn = resp["OpenIDConnectProviderArn"]
        print(f"  [AWS] OIDC Provider Created: {oidc_provider_arn}")
    else:
        print(f"  [AWS] Found Existing OIDC Provider: {oidc_provider_arn}")

    # 2. Build Trust Policy with Trailing Slash aud match
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Federated": oidc_provider_arn
                },
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{AUTH0_DOMAIN}/:aud": AUTH0_CLIENT_ID
                    }
                }
            }
        ]
    }

    # 3. Create or Update IAM Role
    try:
        role_resp = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Dynamic Auth0 Federated Role created via setup_aws_idp.py",
        )
        role_arn = role_resp["Role"]["Arn"]
        print(f"  [AWS] Created IAM Role: {ROLE_NAME} ({role_arn})")
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"  [AWS] IAM Role {ROLE_NAME} already exists. Updating trust policy...")
        iam.update_assume_role_policy(
            RoleName=ROLE_NAME,
            PolicyDocument=json.dumps(trust_policy)
        )
        role_arn = f"arn:aws:iam::{account_id}:role/{ROLE_NAME}"

    # 4. Attach S3 Policy
    try:
        iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=POLICY_ARN)
        print(f"  [AWS] Attached Policy: {POLICY_ARN}")
    except Exception as e:
        print(f"  [AWS] Policy already attached or error: {e}")

    # 5. Save Blueprint State to JSON
    blueprint = {
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "account_id": account_id,
        "region": AWS_REGION,
        "role_name": ROLE_NAME,
        "role_arn": role_arn,
        "oidc_provider_arn": oidc_provider_arn,
        "auth0_domain": AUTH0_DOMAIN,
        "auth0_client_id": AUTH0_CLIENT_ID,
        "attached_policy_arn": POLICY_ARN,
    }

    os.makedirs(os.path.dirname(BLUEPRINT_PATH), exist_ok=True)
    with open(BLUEPRINT_PATH, "w", encoding="utf-8") as f:
        json.dump(blueprint, f, indent=2)

    print(f"  [AWS] Dynamic Blueprint saved to: {BLUEPRINT_PATH}")
    print("=" * 80)
    print("  >>> AWS IDP PROVISIONING COMPLETE")
    print("=" * 80 + "\n")
    return blueprint


if __name__ == "__main__":
    setup_aws_idp()
