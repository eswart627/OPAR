# scripts/deprovision_gcp.py
# Purpose: Deprovision / delete all GCP resources (5 GCS buckets and 8 service accounts).

import os
import sys
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.ops.cleanup_resources import cleanup_gcp, load_model

if __name__ == "__main__":
    print("="*60)
    print(">>> Deprovisioning GCP Resources...")
    print("="*60)
    model = load_model()
    cleanup_gcp(model)
    print(">>> GCP Deprovisioning Complete!")
