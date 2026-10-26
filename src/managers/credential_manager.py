import os
import sys
import json
import time
import urllib.request
import urllib.error
import boto3
from typing import Dict, Any, Optional
from src.adapters.iam.adapter_factory import AdapterFactory

class CredentialManager:
    def __init__(self, base_path: str = "./artifacts", file_name: str = "test_credential.txt"):
        self.base_path = base_path
        self.env_file = os.path.join(self.base_path, file_name)

        self.users: Dict[str, Dict[str, Any]] = {}
        self.ac_managers: Dict[str, AdapterFactory] = {}

        auth_mode = os.environ.get("AUTH_MODE", "static").strip().lower()
        if auth_mode == "auth0":
            print(f"[AUTH][INFO] AUTH_MODE='auth0' detected. Minting dynamic Multi-Cloud STS credentials via Auth0...")
            self._load_auth0_federated_users()
        else:
            print(f"[AUTH][INFO] AUTH_MODE='static' (default). Loading credentials from {self.env_file}...")
            self._load_static_users_and_factories()

    def _load_static_users_and_factories(self) -> None:
        if not os.path.exists(self.env_file):
            print(f"[INFO] {self.env_file} does not exist.")
            return

        with open(self.env_file, "r", encoding="utf-8") as f:
            users_list = json.load(f)
        self.users = {u["name"]: u for u in users_list}

        for user_name, creds in self.users.items():
            factory = AdapterFactory(creds)
            factory.user_name = user_name
            if not hasattr(factory, "get_access_controller"):
                factory.get_access_controller = factory.get
            self.ac_managers[user_name] = factory

    def _load_auth0_federated_users(self) -> None:
        """Dynamically authenticates all blueprint users via Auth0 and mints AWS STS & GCP STS credentials."""
        blueprint_file = os.path.join(self.base_path, "generated_access_model.json")
        if not os.path.exists(blueprint_file):
            print(f"[AUTH][WARN] Blueprint file {blueprint_file} not found. Falling back to static credentials.")
            self._load_static_users_and_factories()
            return

        with open(blueprint_file, "r", encoding="utf-8") as f:
            access_model = json.load(f)

        users_list = access_model.get("USERS", [])
        aws_abac_rules = access_model.get("AWS_ABAC", {})
        gcp_rbac_rules = access_model.get("GCP_RBAC", {})

        auth0_domain = os.environ.get("AUTH0_DOMAIN")
        auth0_client_id = os.environ.get("AUTH0_CLIENT_ID")
        auth0_client_secret = os.environ.get("AUTH0_CLIENT_SECRET")
        auth0_role_arn = os.environ.get("AUTH0_ROLE_ARN")
        aws_region = os.environ.get("AWS_REGION", "ap-south-1")

        gcp_project_id = os.environ.get("GCP_PROJECT_ID", "project-444c1d0a-6028-4d76-8d5")
        gcp_project_number = os.environ.get("GCP_PROJECT_NUMBER", "283433618318")
        gcp_pool_id = os.environ.get("GCP_POOL_ID", "auth0-pool")
        gcp_provider_id = os.environ.get("GCP_PROVIDER_ID", "auth0-provider")
        gcp_sa_email = f"gcp-admin@{gcp_project_id}.iam.gserviceaccount.com"

        default_password = "Corporate@Pass2026!"
        email_domain = "@company.internal"

        sts_client = boto3.client("sts", region_name=aws_region)

        for u in users_list:
            user_name = u["name"]
            email = f"{user_name}{email_domain}"
            aws_attrs = u.get("aws_attributes") or {}
            gcp_roles = u.get("gcp_roles") or []
            full_access = u.get("full_access", False)

            # 1. Determine authorized AWS buckets
            if full_access:
                allowed_aws_buckets = list(aws_abac_rules.keys())
            else:
                allowed_aws_buckets = [
                    b_name for b_name, req_attrs in aws_abac_rules.items()
                    if all(aws_attrs.get(k) == v for k, v in req_attrs.items())
                ]

            # 2. Determine authorized GCP buckets
            if full_access:
                allowed_gcp_buckets = list(gcp_rbac_rules.values())
            else:
                allowed_gcp_buckets = [
                    b_name for role_name, b_name in gcp_rbac_rules.items()
                    if role_name in gcp_roles
                ]

            # 3. Authenticate with Auth0
            token_url = f"https://{auth0_domain}/oauth/token"
            payload = {
                "grant_type": "http://auth0.com/oauth/grant-type/password-realm",
                "username": email,
                "password": default_password,
                "realm": "Username-Password-Authentication",
                "client_id": auth0_client_id,
                "client_secret": auth0_client_secret,
                "scope": "openid profile email",
            }
            req = urllib.request.Request(
                token_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    id_token = json.loads(resp.read().decode("utf-8"))["id_token"]
            except Exception as e:
                print(f"[AUTH][ERROR] Failed to authenticate {email} with Auth0: {e}")
                continue

            # 4. Exchange with AWS STS (with scoped session policy)
            if allowed_aws_buckets:
                statements = [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:ListBucket"],
                        "Resource": [f"arn:aws:s3:::{b}" for b in allowed_aws_buckets] + [f"arn:aws:s3:::{b}/*" for b in allowed_aws_buckets]
                    }
                ]
            else:
                statements = [
                    {
                        "Effect": "Deny",
                        "Action": "s3:*",
                        "Resource": "*"
                    }
                ]
            session_policy = json.dumps({"Version": "2012-10-17", "Statement": statements})

            aws_creds = None
            for attempt in range(3):
                try:
                    sts_resp = sts_client.assume_role_with_web_identity(
                        RoleArn=auth0_role_arn,
                        RoleSessionName=f"rag-session-{user_name}",
                        WebIdentityToken=id_token,
                        Policy=session_policy,
                        DurationSeconds=3600,
                    )
                    aws_creds = sts_resp["Credentials"]
                    break
                except Exception as e:
                    if attempt < 2 and "InvalidIdentityToken" in str(e):
                        time.sleep(1.0)
                        continue
                    print(f"[AUTH][ERROR] AWS STS exchange failed for {user_name}: {e}")

            # 5. Exchange with GCP STS
            gcp_token = None
            try:
                gcp_audience = (
                    f"//iam.googleapis.com/projects/{gcp_project_number}"
                    f"/locations/global/workloadIdentityPools/{gcp_pool_id}/providers/{gcp_provider_id}"
                )
                sts_payload = {
                    "audience": gcp_audience,
                    "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
                    "scope": "https://www.googleapis.com/auth/cloud-platform",
                    "subjectTokenType": "urn:ietf:params:oauth:token-type:id_token",
                    "subjectToken": id_token,
                }
                gcp_req = urllib.request.Request(
                    "https://sts.googleapis.com/v1/token",
                    data=json.dumps(sts_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(gcp_req, timeout=10) as resp:
                    fed_token = json.loads(resp.read().decode("utf-8"))["access_token"]

                imp_url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{gcp_sa_email}:generateAccessToken"
                imp_payload = {"scope": ["https://www.googleapis.com/auth/devstorage.read_only"]}
                imp_req = urllib.request.Request(
                    imp_url,
                    data=json.dumps(imp_payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {fed_token}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(imp_req, timeout=10) as resp:
                    gcp_token = json.loads(resp.read().decode("utf-8"))["accessToken"]
            except Exception as e:
                print(f"[AUTH][ERROR] GCP STS exchange failed for {user_name}: {e}")

            # 6. Store in self.users
            self.users[user_name] = {
                "name": user_name,
                "aws_access_key": aws_creds["AccessKeyId"] if aws_creds else "",
                "aws_secret_key": aws_creds["SecretAccessKey"] if aws_creds else "",
                "aws_session_token": aws_creds["SessionToken"] if aws_creds else "",
                "gcp_credential": {
                    "token": gcp_token,
                    "project_id": gcp_project_id,
                    "allowed_buckets": allowed_gcp_buckets,
                    "full_access": full_access,
                }
            }

            factory = AdapterFactory(self.users[user_name])
            factory.user_name = user_name
            if not hasattr(factory, "get_access_controller"):
                factory.get_access_controller = factory.get
            self.ac_managers[user_name] = factory

        print(f"[AUTH][SUCCESS] Minted dynamic Multi-Cloud STS credentials for {len(self.ac_managers)} users.")

    def get_user_credentials(self, username: str) -> Dict[str, Any]:
        return self.users.get(username, {})

    def get_user_accessible_files(self, user_name: str):
        path = os.path.join(self.base_path, "user_accessible_files.json")
        with open(path, "r", encoding="utf-8") as f:
            access_map = json.load(f)
        return access_map.get(user_name, {})

    def get_ac_manager(self, user_name: str) -> Optional[AdapterFactory]:
        return self.ac_managers.get(user_name)
