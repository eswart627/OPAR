# 02_gcp_aws_resource_creator.py
# Purpose: Provision GCP/AWS buckets, IAM entities, and emit per-user credentials.
# Supports --auth_mode static (default) and --auth_mode auth0.
# Note: Drift queue provisioning is isolated and decoupled from standard pipeline execution.

import os
import sys
import json
import time
import base64
import argparse
from pathlib import Path

# Add project root to sys.path so src and scripts.ops can always be imported
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv
from src.env_loader import load_project_env
load_project_env()
load_dotenv(dotenv_path=os.path.join(project_root, "config", ".env"), override=True)

from googleapiclient.discovery import build
try:
    from oauth2client.service_account import ServiceAccountCredentials
except ImportError:
    ServiceAccountCredentials = None
import boto3

# ---------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "project-444c1d0a-6028-4d76-8d5")
GCP_ADMIN_KEY_PATH = os.environ.get("GCP_ADMIN_KEY_PATH", os.path.join(project_root, "artifacts", "gcp-admin-key.json"))
GCP_BUCKET_LOCATION = os.environ.get("GCP_BUCKET_LOCATION")
GCP_BUCKET_STORAGE_CLASS = os.environ.get("GCP_BUCKET_STORAGE_CLASS", "STANDARD")

AWS_REGION = os.environ.get("AWS_REGION")
AWS_ADMIN_ACCESS_KEY = os.environ.get("AWS_ADMIN_ACCESS_KEY", "")
AWS_ADMIN_SECRET_KEY = os.environ.get("AWS_ADMIN_SECRET_KEY", "")

ARTIFACTS_DIR = Path(project_root) / "artifacts"
ACCESS_MODEL_PATH = ARTIFACTS_DIR / "generated_access_model.json"
CREDENTIAL_OUTPUT_PATH = ARTIFACTS_DIR / "test_credential.txt"

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _ensure_parent_dir(path_str: str) -> None:
    """Create parent directory if it does not exist."""
    p = Path(path_str).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)

def validate_region_prerequisites() -> None:
    """
    Ensure mandatory region environment variables are explicitly defined in config/.env.
    Disallows fallback defaults. Raises ValueError and exits if missing.
    """
    missing = []
    if not os.environ.get("AWS_REGION"):
        missing.append("AWS_REGION (e.g. AWS_REGION=ap-south-1)")
    if not os.environ.get("GCP_BUCKET_LOCATION"):
        missing.append("GCP_BUCKET_LOCATION (e.g. GCP_BUCKET_LOCATION=asia-south1)")

    if missing:
        err_lines = [
            "",
            "=" * 80,
            "  [FATAL PRE-FLIGHT ERROR] MANDATORY REGION CONFIGURATION MISSING",
            "=" * 80,
            "Bucket and queue provisioning strictly requires explicit region configuration.",
            "Fallback default values are disallowed. The following keys are missing in config/.env:",
        ]
        for m in missing:
            err_lines.append(f"    - {m}")
        err_lines.extend([
            "",
            "HOW TO FIX:",
            f"  1. Open: {os.path.join(project_root, 'config', '.env')}",
            "  2. Add the required region keys (e.g., AWS_REGION=ap-south-1 and GCP_BUCKET_LOCATION=asia-south1).",
            "=" * 80,
            "",
        ])
        err_msg = "\n".join(err_lines)
        print(err_msg, file=sys.stderr)
        raise ValueError(f"Mandatory region configuration missing: {', '.join(missing)}")

# ---------------------------------------------------------------------
# Load access model
# ---------------------------------------------------------------------

if os.path.exists(ACCESS_MODEL_PATH):
    with open(ACCESS_MODEL_PATH, "r", encoding="utf-8") as f:
        access_model = json.load(f)
    GCP_RBAC = access_model.get("GCP_RBAC", {})
    AWS_ABAC = access_model.get("AWS_ABAC", {})
    USERS = access_model.get("USERS", [])
    USER_GROUPS = access_model.get("USER_GROUPS", {})
else:
    print(f"[WARN] Access model not found at {ACCESS_MODEL_PATH}. Using standard blueprint user roster.")
    GCP_RBAC = {}
    AWS_ABAC = {}
    USERS = [
        {"name": f"user-{i}", "gcp_roles": None, "aws_attributes": None}
        for i in range(1, 8)
    ] + [{"name": "baseline-admin", "full_access": True}]
    USER_GROUPS = {}

# ---------------------------------------------------------------------
# SDK clients
# ---------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
if GCP_ADMIN_KEY_PATH and os.path.exists(GCP_ADMIN_KEY_PATH):
    gcp_credentials = ServiceAccountCredentials.from_json_keyfile_name(GCP_ADMIN_KEY_PATH, SCOPES)
else:
    import google.auth
    try:
        gcp_credentials, _ = google.auth.default(scopes=SCOPES)
    except Exception:
        gcp_credentials = None

gcp_storage = build("storage", "v1", credentials=gcp_credentials) if gcp_credentials else None
gcp_iam = build("iam", "v1", credentials=gcp_credentials) if gcp_credentials else None

aws_s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ADMIN_ACCESS_KEY,
    aws_secret_access_key=AWS_ADMIN_SECRET_KEY,
    region_name=AWS_REGION,
) if AWS_ADMIN_ACCESS_KEY else None

aws_iam = boto3.client(
    "iam",
    aws_access_key_id=AWS_ADMIN_ACCESS_KEY,
    aws_secret_access_key=AWS_ADMIN_SECRET_KEY,
    region_name=AWS_REGION,
) if AWS_ADMIN_ACCESS_KEY else None

# ---------------------------------------------------------------------
# GCP
# ---------------------------------------------------------------------

def create_gcp_buckets() -> None:
    """Create GCP buckets if they do not exist."""
    validate_region_prerequisites()
    if not gcp_storage:
        print("[GCP][WARN] GCP Storage client not available. Skipping bucket creation.")
        return
    for bucket_name in set(GCP_RBAC.values()):
        try:
            gcp_storage.buckets().insert(
                project=GCP_PROJECT_ID,
                body={
                    "name": bucket_name,
                    "location": GCP_BUCKET_LOCATION,
                    "storageClass": GCP_BUCKET_STORAGE_CLASS,
                },
            ).execute()
            print(f"[GCP][INFO] Created bucket: {bucket_name}")
        except Exception as e:
            msg = str(e)
            if "already exists" in msg:
                print(f"[GCP][INFO] Bucket already exists: {bucket_name}")
            else:
                print(f"[GCP][ERROR] Error creating bucket {bucket_name}: {e}")

def setup_gcp_users() -> dict:
    """Create service accounts, bind storage.objectViewer to target buckets, and issue keys."""
    result = {}
    if not gcp_iam or not gcp_storage:
        print("[GCP][WARN] GCP IAM or Storage client not available. Skipping user setup.")
        return result

    for user in USERS:
        name = user["name"]
        roles = user.get("gcp_roles") or []
        full_access = user.get("full_access", False)

        sa_id = name.lower()
        sa_email = f"{sa_id}@{GCP_PROJECT_ID}.iam.gserviceaccount.com"
        sa_resource = f"projects/{GCP_PROJECT_ID}/serviceAccounts/{sa_email}"

        # Create service account with simple backoff on quota
        for attempt in range(5):
            try:
                gcp_iam.projects().serviceAccounts().create(
                    name=f"projects/{GCP_PROJECT_ID}",
                    body={"accountId": sa_id, "serviceAccount": {"displayName": sa_id}},
                ).execute()
                print(f"[GCP][INFO] Created service account: {sa_email}")
                break
            except Exception as e:
                emsg = str(e).lower()
                if "quota" in emsg or "per minute" in emsg:
                    print("[GCP][WARN] Quota reached, sleeping 60s for retry...")
                    time.sleep(60)
                elif "already exists" in emsg:
                    print(f"[GCP][INFO] Service account already exists: {sa_email}")
                    break
                else:
                    print(f"[GCP][ERROR] Error creating service account {sa_id}: {e}")
                    break

        # Wait until service account is available
        for _ in range(10):
            try:
                gcp_iam.projects().serviceAccounts().get(name=sa_resource).execute()
                time.sleep(5)
                break
            except Exception:
                time.sleep(10)

        # Determine target buckets
        target_buckets = list(GCP_RBAC.values()) if full_access else [GCP_RBAC[r] for r in roles]

        # Grant roles/storage.objectViewer on target buckets
        for bucket_name in target_buckets:
            try:
                gcp_iam.projects().serviceAccounts().get(name=sa_resource).execute()
            except Exception as e:
                print(f"[GCP][ERROR] Skipping policy for {bucket_name}; service account not found: {e}")
                continue

            policy = gcp_storage.buckets().getIamPolicy(bucket=bucket_name).execute()
            member = f"serviceAccount:{sa_email}"
            role_key = "roles/storage.objectViewer"
            binding_exists = False

            for b in policy.get("bindings", []):
                if b["role"] == role_key:
                    if member not in b["members"]:
                        b["members"].append(member)
                    binding_exists = True
                    break

            if not binding_exists:
                policy.setdefault("bindings", []).append({"role": role_key, "members": [member]})

            for attempt in range(5):
                try:
                    gcp_storage.buckets().setIamPolicy(bucket=bucket_name, body=policy).execute()
                    break
                except Exception as e:
                    print(f"[GCP][WARN] Retry {attempt + 1} setIamPolicy for {bucket_name}: {e}")
                    time.sleep(2)

        # Issue key file for the service account (or fallback to ADC identity mapping)
        try:
            key = gcp_iam.projects().serviceAccounts().keys().create(
                name=sa_resource,
                body={"privateKeyType": "TYPE_GOOGLE_CREDENTIALS_FILE"},
            ).execute()
            key_json = json.loads(base64.b64decode(key["privateKeyData"]).decode("utf-8"))
            result[name] = key_json
        except Exception as e:
            print(f"[GCP][INFO] Key creation skipped for {name}. Using ADC identity mapping.")
            result[name] = {
                "service_account_email": sa_email,
                "project_id": GCP_PROJECT_ID,
                "allowed_buckets": target_buckets,
                "full_access": full_access,
            }

    return result

# ---------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------

def create_aws_buckets() -> None:
    """Create S3 buckets if they do not exist."""
    validate_region_prerequisites()
    if not aws_s3:
        print("[AWS][WARN] AWS S3 client not available. Skipping bucket creation.")
        return
    for bucket_name in AWS_ABAC.keys():
        try:
            if AWS_REGION == "us-east-1":
                aws_s3.create_bucket(Bucket=bucket_name)
            else:
                aws_s3.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": AWS_REGION},
                )
            print(f"[AWS][INFO] Created bucket: {bucket_name}")
        except aws_s3.exceptions.BucketAlreadyOwnedByYou:
            print(f"[AWS][INFO] Bucket already exists: {bucket_name}")
        except Exception as e:
            print(f"[AWS][ERROR] Error creating bucket {bucket_name}: {e}")

def setup_aws_users_and_abac_groups() -> dict:
    """
    Create ABAC-oriented IAM group(s), attach bucket policies conditioned on tags,
    and add users to groups per USER_GROUPS.
    """
    result = {}
    if not aws_iam:
        print("[AWS][WARN] AWS IAM client not available. Skipping group setup.")
        return result

    created_groups = set()

    # Create groups
    for group_name in USER_GROUPS:
        try:
            aws_iam.create_group(GroupName=group_name)
            print(f"[AWS][INFO] Created group: {group_name}")
        except aws_iam.exceptions.EntityAlreadyExistsException:
            print(f"[AWS][INFO] Group already exists: {group_name}")
        created_groups.add(group_name)

    # Add users to groups
    for group_name, members in USER_GROUPS.items():
        for user_name in members:
            try:
                aws_iam.add_user_to_group(GroupName=group_name, UserName=user_name)
                print(f"[AWS][INFO] Added {user_name} to group {group_name}")
            except Exception as e:
                print(f"[AWS][ERROR] Failed to add {user_name} to group {group_name}: {e}")

    # Create and attach policies derived from AWS_ABAC
    for bucket_name, attr_dict in AWS_ABAC.items():
        condition = {f"aws:PrincipalTag/{k}": v for k, v in attr_dict.items()}
        safe_sid = f"Access{''.join(filter(str.isalnum, bucket_name))}"
        policy_doc = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": safe_sid,
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [
                        f"arn:aws:s3:::{bucket_name}",
                        f"arn:aws:s3:::{bucket_name}/*",
                    ],
                    "Condition": {"StringEquals": condition},
                }
            ],
        }

        policy_name = f"abac-{bucket_name}-policy"
        try:
            response = aws_iam.create_policy(PolicyName=policy_name, PolicyDocument=json.dumps(policy_doc))
            policy_arn = response["Policy"]["Arn"]
            print(f"[AWS][INFO] Created policy: {policy_name}")
        except aws_iam.exceptions.EntityAlreadyExistsException:
            account_id = boto3.client(
                "sts",
                aws_access_key_id=AWS_ADMIN_ACCESS_KEY,
                aws_secret_access_key=AWS_ADMIN_SECRET_KEY,
                region_name=AWS_REGION,
            ).get_caller_identity()["Account"]
            policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
            print(f"[AWS][INFO] Policy already exists: {policy_name}")

        for group_name in created_groups:
            try:
                aws_iam.attach_group_policy(GroupName=group_name, PolicyArn=policy_arn)
                print(f"[AWS][INFO] Attached policy {policy_name} to group {group_name}")
            except Exception as e:
                print(f"[AWS][ERROR] Failed to attach policy {policy_name} to group {group_name}: {e}")

    return result

def create_aws_users() -> dict:
    """Create IAM users, tag them for ABAC, optionally attach full S3 access, and issue access keys."""
    result = {}
    if not aws_iam:
        print("[AWS][WARN] AWS IAM client not available. Skipping user creation.")
        return result

    for user in USERS:
        name = user["name"]
        tags = user.get("aws_attributes") or {}
        full_access = user.get("full_access", False)

        tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]
        try:
            aws_iam.create_user(UserName=name, Tags=tag_list)
            print(f"[AWS][INFO] Created user: {name}")
        except aws_iam.exceptions.EntityAlreadyExistsException:
            print(f"[AWS][INFO] User already exists: {name}")

        if full_access:
            try:
                policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
                aws_iam.attach_user_policy(UserName=name, PolicyArn=policy_arn)
                print(f"[AWS][INFO] Attached AmazonS3FullAccess to {name}")
            except Exception as e:
                print(f"[AWS][ERROR] Failed to attach full access to {name}: {e}")

        try:
            key = aws_iam.create_access_key(UserName=name)["AccessKey"]
            result[name] = {
                "aws_access_key": key["AccessKeyId"],
                "aws_secret_key": key["SecretAccessKey"],
            }
        except Exception as e:
            print(f"[AWS][ERROR] Failed to create access key for {name}: {e}")

    return result

# ---------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------

def setup_gcp() -> None:
    """Provision GCP resources and write partial credential file."""
    create_gcp_buckets()
    gcp_keys = setup_gcp_users()

    merged = []
    for user in USERS:
        name = user["name"]
        merged.append(
            {
                "name": name,
                "gcp_credential": gcp_keys.get(name, {}),
                "aws_access_key": "",
                "aws_secret_key": "",
            }
        )

    _ensure_parent_dir(CREDENTIAL_OUTPUT_PATH)
    with open(CREDENTIAL_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    print(f"[GCP][INFO] GCP credentials saved to {CREDENTIAL_OUTPUT_PATH}")

def setup_aws() -> None:
    """Provision AWS resources and append access keys into the credential file."""
    create_aws_buckets()
    aws_keys = create_aws_users()
    setup_aws_users_and_abac_groups()

    with open(CREDENTIAL_OUTPUT_PATH, "r", encoding="utf-8") as f:
        merged = json.load(f)

    for user in merged:
        uname = user["name"]
        user["aws_access_key"] = aws_keys.get(uname, {}).get("aws_access_key", "")
        user["aws_secret_key"] = aws_keys.get(uname, {}).get("aws_secret_key", "")

    with open(CREDENTIAL_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    print(f"[AWS][INFO] AWS credentials appended to {CREDENTIAL_OUTPUT_PATH}")

def setup_auth0() -> None:
    """
    Provision multi-cloud storage buckets, sync users to Auth0 Cloud Directory,
    and configure AWS OIDC dynamic role federation.
    Emits federated identity roster to test_credential.txt without permanent IAM keys.
    """
    print("\n" + "=" * 80)
    print(">>> Provisioning Multi-Cloud Infrastructure in AUTH0 Federated Mode...")
    print("=" * 80)

    # 1. Create S3 & GCS Buckets (shared data plane across all modes)
    create_gcp_buckets()
    create_aws_buckets()

    # 2. Sync all blueprint users to Auth0 Directory
    print("\n[AUTH0][INFO] Syncing blueprint users to Auth0 Cloud Directory...")
    try:
        from scripts.ops.identity.auth0_create_user import create_all_blueprint_users
        create_all_blueprint_users()
    except Exception as e:
        print(f"[AUTH0][ERROR] Failed to sync users to Auth0: {e}")

    # 3. Setup AWS OIDC dynamic federation role and policy
    print("\n[AUTH0][INFO] Setting up AWS OIDC Identity Provider & Role...")
    try:
        from scripts.ops.identity.setup_aws_idp import setup_aws_idp
        setup_aws_idp()
    except Exception as e:
        print(f"[AUTH0][ERROR] Failed to set up AWS IDP: {e}")

    # 4. Emit federated user roster to test_credential.txt
    _ensure_parent_dir(CREDENTIAL_OUTPUT_PATH)
    federated_users = [
        {
            "name": u["name"],
            "auth_mode": "auth0",
            "status": "federated",
            "aws_access_key": "",
            "aws_secret_key": "",
            "gcp_credential": {},
        }
        for u in USERS
    ]
    with open(CREDENTIAL_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(federated_users, f, indent=2)
    print(f"\n[AUTH0][INFO] Auth0 federated user roster saved to {CREDENTIAL_OUTPUT_PATH}")

def setup_drift_queues() -> None:
    """
    Set up AWS SQS and GCP Pub/Sub drift notification queues.
    (Note: Decoupled from default 02 pipeline flow. Kept for manual operational import).
    """
    print("\n" + "=" * 80)
    print(">>> Provisioning Multi-Cloud Drift Notification Queues (SQS & Pub/Sub)...")
    print("=" * 80)
    try:
        try:
            from scripts.ops.drift.setup_aws_drift_queue import setup_sqs_and_notifications
        except ModuleNotFoundError:
            from ops.drift.setup_aws_drift_queue import setup_sqs_and_notifications
        setup_sqs_and_notifications()
    except Exception as e:
        print(f"[AWS][ERROR] Failed to set up SQS drift queue: {e}")

    try:
        try:
            from scripts.ops.drift.setup_gcp_drift_queue import setup_gcp_pubsub
        except ModuleNotFoundError:
            from ops.drift.setup_gcp_drift_queue import setup_gcp_pubsub
        setup_gcp_pubsub()
    except Exception as e:
        print(f"[GCP][ERROR] Failed to set up GCP Pub/Sub drift queue: {e}")

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    validate_region_prerequisites()
    parser = argparse.ArgumentParser(description="02: Multi-Cloud Resource & Identity Provisioner")
    parser.add_argument(
        "--auth_mode",
        choices=["static", "auth0"],
        default=os.environ.get("AUTH_MODE", "static").strip().lower(),
        help="Identity federation bridge mode: 'static' (IAM users + access keys) or 'auth0' (Cloud IDP federation). Default: static",
    )
    args = parser.parse_args()
    mode = args.auth_mode.lower()
    os.environ["AUTH_MODE"] = mode

    print("=" * 80)
    print(f">>> Executing 02_gcp_aws_resource_creator.py in Mode: [{mode.upper()}]")
    print("=" * 80)

    if mode == "auth0":
        setup_auth0()
    else:
        setup_gcp()
        setup_aws()

    print("\n" + "=" * 80)
    print(f">>> Multi-Cloud Resource Provisioning Complete for Mode: [{mode.upper()}]")
    print("=" * 80)

if __name__ == "__main__":
    main()
