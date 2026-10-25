# scripts/deprovision_aws.py
# Purpose: Deprovision / delete all AWS resources (5 S3 buckets, IAM users, groups, and ABAC policies).

import os
import sys
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.ops.cleanup_resources import cleanup_aws, load_model

if __name__ == "__main__":
    print("="*60)
    print(">>> Deprovisioning AWS Resources...")
    print("="*60)
    model = load_model()
    cleanup_aws(model)
    print(">>> AWS Deprovisioning Complete!")
