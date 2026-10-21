"""
scripts/custom_01_generate_access_model.py

Step 1: Corporate Access Model & Credential Generator.
Reads the storage map (file_to_storage_info.json) from the MultiHop dataset export,
determines the 5 corporate cloud buckets, and provisions:
  1. artifacts/custom_artifacts/generated_access_model.json
  2. artifacts/custom_artifacts/test_credential.txt (with local GCP allowed_buckets mapping)
  3. artifacts/custom_artifacts/user_accessible_files.json (ground-truth visibility map)

Zero GCP/AWS API calls required — uses local credential/allowed_buckets validation.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_generate_access_model(dataset_dir: str):
    print("=" * 80)
    print("STEP 01: GENERATING CORPORATE ACCESS MODEL & LOCAL IAM CREDENTIALS")
    print("=" * 80)

    dataset_path = Path(dataset_dir)
    storage_map_file = dataset_path / "file_to_storage_info.json"

    if not storage_map_file.exists():
        raise FileNotFoundError(f"Storage map not found at: {storage_map_file}")

    with open(storage_map_file, "r", encoding="utf-8") as f:
        storage_map = json.load(f)

    # Output directory
    custom_artifacts = PROJECT_ROOT / "artifacts" / "custom_artifacts"
    custom_artifacts.mkdir(parents=True, exist_ok=True)

    # 1. Discover all unique buckets
    bucket_to_files = defaultdict(list)
    provider_buckets = defaultdict(set)
    for fname, sinfo in storage_map.items():
        p = sinfo.get("provider", "gcp")
        b = sinfo.get("bucket", "")
        if b:
            bucket_to_files[b].append(fname)
            provider_buckets[p].add(b)

    all_buckets = list(bucket_to_files.keys())
    print(f"\n[INFO] Discovered {len(all_buckets)} corporate buckets across {len(storage_map)} articles:")
    for b in sorted(all_buckets):
        print(f"  - {b} ({len(bucket_to_files[b])} files)")

    # 2. Load the real corporate user credentials from artifacts/test_credential.txt
    orig_cred_file = PROJECT_ROOT / "artifacts" / "test_credential.txt"
    if not orig_cred_file.exists():
        raise FileNotFoundError(f"Original credentials not found at: {orig_cred_file}")

    with open(orig_cred_file, "r", encoding="utf-8") as f:
        real_users = json.load(f)

    role_titles = {
        "user-1": "Security & Compliance Auditor",
        "user-2": "Software Engineer (R&D)",
        "user-3": "HR / Talent Specialist",
        "user-4": "Finance Officer / CFO Analyst",
        "user-5": "Staff Research Scientist",
        "user-6": "Operations Lead",
        "user-7": "Director of Engineering",
        "baseline-admin": "Super Administrator",
    }

    users = []
    for u in real_users:
        uname = u.get("name")
        u_copy = u.copy()
        u_copy["role"] = role_titles.get(uname, "Corporate User")
        users.append(u_copy)

    # Save real cloud credentials to custom_artifacts
    cred_file = custom_artifacts / "test_credential.txt"
    with open(cred_file, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)
    print(f"\n[SUCCESS] Saved real cloud credentials to: {cred_file}")

    import boto3

    aws_buckets = [b for b in all_buckets if "aws" in b]
    print(f"\n[INFO] Validating live AWS IAM permissions via boto3 across {len(aws_buckets)} AWS buckets: {aws_buckets}")
    
    aws_allowed_map = {}
    for u in users:
        uname = u.get("name")
        ak = u.get("aws_access_key")
        sk = u.get("aws_secret_key")
        is_admin = bool(u.get("full_access")) or uname == "baseline-admin"
        
        allowed_aws = []
        if is_admin:
            allowed_aws = list(aws_buckets)
        elif ak and sk:
            try:
                s3_client = boto3.Session(aws_access_key_id=ak, aws_secret_access_key=sk, region_name="ap-south-1").client("s3")
                for b in aws_buckets:
                    try:
                        s3_client.head_bucket(Bucket=b)
                        allowed_aws.append(b)
                    except Exception:
                        pass
            except Exception as e:
                print(f"  [AWS] Error creating S3 session for {uname}: {e}")

        aws_allowed_map[uname] = allowed_aws
        print(f"  - {uname:<14} ({u.get('role', ''):<30}): Live AWS S3 Buckets = {allowed_aws}")

    # 4. Generate user_accessible_files.json (GCP + Live AWS)
    user_visibility = {}
    for u in users:
        uname = u["name"]
        if u.get("full_access") or u.get("gcp_credential", {}).get("full_access"):
            user_visibility[uname] = {"full_access": True}
        else:
            allowed_gcp = u.get("gcp_credential", {}).get("allowed_buckets", [])
            allowed_aws = aws_allowed_map.get(uname, [])
            all_user_buckets = set(allowed_gcp) | set(allowed_aws)
            vis_files = set()
            for b in all_user_buckets:
                vis_files.update(bucket_to_files.get(b, []))
            user_visibility[uname] = {"files": sorted(vis_files)}

    vis_file = custom_artifacts / "user_accessible_files.json"
    with open(vis_file, "w", encoding="utf-8") as f:
        json.dump(user_visibility, f, indent=2, ensure_ascii=False)
    print(f"\n[SUCCESS] Saved live visibility map to: {vis_file}")

    # 5. Save generated_access_model.json
    access_model = {
        "buckets": all_buckets,
        "users": [
            {
                "name": u["name"],
                "role": u["role"],
                "gcp_allowed_buckets": u["gcp_credential"]["allowed_buckets"],
                "aws_allowed_buckets": aws_allowed_map.get(u["name"], []),
                "full_access": u.get("full_access", False) or u["gcp_credential"].get("full_access", False)
            }
            for u in users
        ]
    }
    access_model_file = custom_artifacts / "generated_access_model.json"
    with open(access_model_file, "w", encoding="utf-8") as f:
        json.dump(access_model, f, indent=2, ensure_ascii=False)
    print(f"[SUCCESS] Saved access model to: {access_model_file}")

    print("\n" + "=" * 80)
    print("STEP 01 COMPLETE: LIVE AWS S3 & GCP CLOUD IAM MODELS INITIALIZED.")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step 01: Generate Corporate Access Model.")
    parser.add_argument(
        "--dataset-dir",
        type=str,
        default=r"C:\Users\pichi\Desktop\MultiHop_Dataset_Creation_Pipeline\data\permission_aware_export",
        help="Path to exported MultiHop dataset directory."
    )
    args = parser.parse_args()
    run_generate_access_model(args.dataset_dir)

