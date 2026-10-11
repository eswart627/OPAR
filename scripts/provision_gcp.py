# scripts/provision_gcp.py
# Purpose: Provision only GCP resources (5 GCS buckets, 8 service accounts, IAM bindings).

import os
import sys
import importlib
from pathlib import Path

# Add project root to sys.path
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

creator_mod = importlib.import_module("scripts.02_gcp_aws_resource_creator")

if __name__ == "__main__":
    print("="*60)
    print(">>> Provisioning GCP Resources...")
    print("="*60)
    creator_mod.setup_gcp()
    print(">>> GCP Provisioning Complete!")
