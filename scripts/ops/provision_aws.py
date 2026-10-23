# scripts/provision_aws.py
# Purpose: Provision only AWS resources (5 S3 buckets, 7 IAM users, ABAC groups, tags, and policies).

import os
import sys
import importlib
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

creator_mod = importlib.import_module("scripts.02_gcp_aws_resource_creator")

if __name__ == "__main__":
    print("="*60)
    print(">>> Provisioning AWS Resources...")
    print("="*60)
    creator_mod.setup_aws()
    print(">>> AWS Provisioning Complete!")
