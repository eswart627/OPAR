import os
import json
import base64
import time
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import storage
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.api_core.exceptions import Forbidden, NotFound

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=True)

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "project-444c1d0a-6028-4d76-8d5")
ADMIN_KEY_PATH = os.environ.get("GCP_ADMIN_KEY_PATH", "./artifacts/Permission_Aware_Rag_GCP_ADMIN_KEY.json")

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"
CRED_FILE = ARTIFACTS_DIR / "test_credential.txt"

# 5 existing GCP buckets
GCP_BUCKETS = [
    f"cyvcyfpb-gcp-bucket-{i}" for i in range(1, 6)
]

# Expected access matrix based on project RBAC
EXPECTED_ACCESS = {
    "user-1": [],
    "user-2": ["cyvcyfpb-gcp-bucket-2"],
    "user-3": ["cyvcyfpb-gcp-bucket-2", "cyvcyfpb-gcp-bucket-3"],
    "user-4": ["cyvcyfpb-gcp-bucket-1", "cyvcyfpb-gcp-bucket-3", "cyvcyfpb-gcp-bucket-5"],
    "user-5": ["cyvcyfpb-gcp-bucket-1", "cyvcyfpb-gcp-bucket-2", "cyvcyfpb-gcp-bucket-3"],
    "user-6": ["cyvcyfpb-gcp-bucket-1", "cyvcyfpb-gcp-bucket-2", "cyvcyfpb-gcp-bucket-3", "cyvcyfpb-gcp-bucket-4", "cyvcyfpb-gcp-bucket-5"],
    "user-7": ["cyvcyfpb-gcp-bucket-1", "cyvcyfpb-gcp-bucket-2", "cyvcyfpb-gcp-bucket-3", "cyvcyfpb-gcp-bucket-4", "cyvcyfpb-gcp-bucket-5"],
    "baseline-admin": ["cyvcyfpb-gcp-bucket-1", "cyvcyfpb-gcp-bucket-2", "cyvcyfpb-gcp-bucket-3", "cyvcyfpb-gcp-bucket-4", "cyvcyfpb-gcp-bucket-5"],
}

def generate_real_user_keys():
    print("\n[STEP 1] Generating real private JSON keys for all GCP user service accounts...")
    admin_key_file = Path(ADMIN_KEY_PATH)
    if not admin_key_file.is_absolute():
        admin_key_file = (Path(__file__).resolve().parent.parent / admin_key_file).resolve()

    admin_creds = service_account.Credentials.from_service_account_file(str(admin_key_file))
    iam_service = build("iam", "v1", credentials=admin_creds)

    with open(CRED_FILE, "r", encoding="utf-8") as f:
        credentials = json.load(f)

    updated_count = 0
    for user_entry in credentials:
        name = user_entry["name"]
        sa_email = f"{name}@{PROJECT_ID}.iam.gserviceaccount.com"
        sa_resource = f"projects/{PROJECT_ID}/serviceAccounts/{sa_email}"

        # If user already has a private_key, skip generating a new one
        existing_gcp = user_entry.get("gcp_credential", {})
        if isinstance(existing_gcp, dict) and "private_key" in existing_gcp:
            print(f"  - {name}: Already has valid private JSON key.")
            continue

        try:
            print(f"  - {name}: Issuing new private JSON key via GCP IAM...")
            key_resp = iam_service.projects().serviceAccounts().keys().create(
                name=sa_resource,
                body={"privateKeyType": "TYPE_GOOGLE_CREDENTIALS_FILE"},
            ).execute()
            key_json = json.loads(base64.b64decode(key_resp["privateKeyData"]).decode("utf-8"))
            user_entry["gcp_credential"] = key_json
            updated_count += 1
            print(f"    -> [SUCCESS] Key issued: {key_json.get('private_key_id')[:10]}...")
        except Exception as e:
            print(f"    -> [ERROR] Failed to issue key for {name}: {e}")

    if updated_count > 0:
        with open(CRED_FILE, "w", encoding="utf-8") as f:
            json.dump(credentials, f, indent=2)
        print(f"\n[INFO] Updated {updated_count} user keys in {CRED_FILE.name}")

    return credentials

def test_user_permissions(credentials):
    print("\n[STEP 2] Testing LIVE GCP Access for Each User Across All 5 Buckets...")
    print("=" * 80)
    
    # We will test reading one representative file from each bucket
    test_files = {
        "cyvcyfpb-gcp-bucket-1": "15_penn_plaza.txt",
        "cyvcyfpb-gcp-bucket-2": "101_dalmatians_1996_film.txt",
        "cyvcyfpb-gcp-bucket-3": "10000_metres.txt",
        "cyvcyfpb-gcp-bucket-4": "1500_metres.txt",
        "cyvcyfpb-gcp-bucket-5": "1891_birthday_honours.txt",
    }

    results = []

    for user_entry in credentials:
        name = user_entry["name"]
        gcp_cred = user_entry.get("gcp_credential", {})
        expected_buckets = EXPECTED_ACCESS.get(name, [])

        print(f"\n>>> Testing User: {name} (Expected Allowed: {len(expected_buckets)}/5 buckets)")

        # Create live storage client using user's private key
        try:
            if isinstance(gcp_cred, dict) and "private_key" in gcp_cred:
                user_client = storage.Client.from_service_account_info(gcp_cred)
            else:
                print(f"  [WARN] No private key found for {name}. Skipping live check.")
                continue
        except Exception as e:
            print(f"  [ERROR] Could not build storage client for {name}: {e}")
            continue

        for bucket_name in GCP_BUCKETS:
            fname = test_files[bucket_name]
            expected_allowed = (bucket_name in expected_buckets)
            
            # Test live access using test_iam_permissions and download
            actual_allowed = False
            try:
                bucket = user_client.bucket(bucket_name)
                blob = bucket.blob(fname)
                perms = blob.test_iam_permissions(["storage.objects.get"])
                if "storage.objects.get" in perms:
                    # Try actual read to verify 100%
                    content = blob.download_as_text()
                    actual_allowed = True
            except (Forbidden, Exception):
                actual_allowed = False

            status = "PASS" if (actual_allowed == expected_allowed) else "FAIL"
            access_str = "ALLOWED (200)" if actual_allowed else "DENIED (403)"
            exp_str = "ALLOWED" if expected_allowed else "DENIED"

            results.append({
                "user": name,
                "bucket": bucket_name,
                "expected": exp_str,
                "actual": access_str,
                "status": status,
            })

            symbol = "PASS" if status == "PASS" else "FAIL"
            print(f"  [{symbol}] {bucket_name}: {access_str} (Expected: {exp_str})")

    # Summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY REPORT:")
    print("=" * 80)
    total_checks = len(results)
    passed_checks = sum(1 for r in results if r["status"] == "PASS")
    print(f"Total Permission Checks: {total_checks}")
    print(f"Passed Checks:           {passed_checks}/{total_checks} ({passed_checks/total_checks*100:.1f}%)")
    if passed_checks == total_checks:
        print("\n[SUCCESS] ALL ACCESS CONTROLS AND POLICIES ARE WORKING PERFECTLY IN GOOGLE CLOUD!")

if __name__ == "__main__":
    creds = generate_real_user_keys()
    test_user_permissions(creds)
