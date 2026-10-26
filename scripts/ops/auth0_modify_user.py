# scripts/ops/auth0_modify_user.py
# Purpose: Syncs and updates all blueprint users' attributes in Auth0 from generated_access_model.json.

import os
import sys
import json
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
load_dotenv(os.path.join(project_root, "config", ".env"), override=True)

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET")
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


def modify_all_blueprint_users():
    blueprint_path = os.path.join(project_root, "artifacts", "generated_access_model.json")
    if not os.path.exists(blueprint_path):
        print(f"[ERROR] Blueprint not found at: {blueprint_path}")
        return False

    with open(blueprint_path, "r", encoding="utf-8") as f:
        access_model = json.load(f)

    users = access_model.get("USERS", [])
    print("=" * 80)
    print(f"  UPDATING / SYNCING {len(users)} BLUEPRINT USERS IN AUTH0")
    print("=" * 80)

    token = get_mgmt_token()

    for u in users:
        user_name = u["name"]
        email = f"{user_name}{EMAIL_DOMAIN}"
        aws_attrs = u.get("aws_attributes") or {}
        gcp_roles = u.get("gcp_roles") or []
        full_access = u.get("full_access", False)

        # Find user ID by email
        q = urllib.parse.quote(f'email:"{email}"')
        search_url = f"https://{AUTH0_DOMAIN}/api/v2/users?q={q}&search_engine=v3"
        req = urllib.request.Request(
            search_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read().decode("utf-8"))

        if not results:
            print(f"  [NOT FOUND] {email:<30} (User does not exist in Auth0)")
            continue

        user_id = results[0]["user_id"]

        patch_url = f"https://{AUTH0_DOMAIN}/api/v2/users/{urllib.parse.quote(user_id)}"
        patch_payload = {
            "app_metadata": {
                "department": "Corporate",
                "full_access": full_access,
                "aws_attributes": aws_attrs,
                "gcp_roles": gcp_roles,
            }
        }
        patch_req = urllib.request.Request(
            patch_url,
            data=json.dumps(patch_payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="PATCH",
        )
        with urllib.request.urlopen(patch_req, timeout=10) as resp:
            json.loads(resp.read().decode("utf-8"))

        print(f"  [UPDATED]   {email:<30} (Attrs: {aws_attrs}, Roles: {gcp_roles})")

    print("=" * 80)
    print("  >>> COMPLETED: All Blueprint Users Synced with Fresh Permissions.")
    print("=" * 80 + "\n")
    return True


if __name__ == "__main__":
    modify_all_blueprint_users()
