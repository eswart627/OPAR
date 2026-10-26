# scripts/ops/auth0_create_user.py
# Purpose: Reads generated_access_model.json and creates all blueprint users in Auth0.

import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET")
CONNECTION_NAME = "Username-Password-Authentication"
DEFAULT_PASSWORD = os.environ.get("AUTH0_DEFAULT_PASSWORD", "Corporate@Pass2026!")
EMAIL_DOMAIN = "@company.internal"

if not AUTH0_DOMAIN or not AUTH0_CLIENT_ID or not AUTH0_CLIENT_SECRET:
    raise ValueError("Missing required Auth0 environment variables in config/.env")


def get_mgmt_token() -> str:
    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": AUTH0_CLIENT_ID,
        "client_secret": AUTH0_CLIENT_SECRET,
        "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
    }
    req = urllib.request.Request(
        token_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["access_token"]


def create_all_blueprint_users():
    blueprint_path = os.path.join(project_root, "artifacts", "generated_access_model.json")
    if not os.path.exists(blueprint_path):
        print(f"[ERROR] Blueprint not found at: {blueprint_path}")
        return False

    with open(blueprint_path, "r", encoding="utf-8") as f:
        access_model = json.load(f)

    users = access_model.get("USERS", [])
    print("=" * 80)
    print(f"  CREATING {len(users)} BLUEPRINT USERS IN AUTH0")
    print("=" * 80)

    token = get_mgmt_token()
    url = f"https://{AUTH0_DOMAIN}/api/v2/users"

    success_count = 0
    for u in users:
        user_name = u["name"]
        email = f"{user_name}{EMAIL_DOMAIN}"
        aws_attrs = u.get("aws_attributes") or {}
        gcp_roles = u.get("gcp_roles") or []
        full_access = u.get("full_access", False)

        payload = {
            "connection": CONNECTION_NAME,
            "email": email,
            "password": DEFAULT_PASSWORD,
            "email_verified": True,
            "app_metadata": {
                "department": "Corporate",
                "full_access": full_access,
                "aws_attributes": aws_attrs,
                "gcp_roles": gcp_roles,
            },
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            print(f"  [CREATED] {email:<30} -> ID: {data.get('user_id')}")
            if full_access:
                print(f"            Full Access: True")
            else:
                print(f"            AWS Attrs: {aws_attrs}")
                print(f"            GCP Roles: {gcp_roles}")
            success_count += 1
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8")
            if "The user already exists" in err:
                print(f"  [EXISTS]  {email:<30} (User already registered)")
                success_count += 1
            else:
                print(f"  [ERROR]   {email:<30} Failed ({e.code}): {err}")

    print("=" * 80)
    print(f"  >>> COMPLETED: {success_count}/{len(users)} Blueprint Users Ready in Auth0.")
    print("=" * 80 + "\n")
    return True


if __name__ == "__main__":
    create_all_blueprint_users()
