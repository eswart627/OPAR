# tests/test_strict_region_enforcement.py
# Purpose: Verify that missing AWS_REGION or GCP_BUCKET_LOCATION strictly halts
# execution and raises fatal pre-flight errors without any fallback values.

import os
import sys
import subprocess
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

def test_missing_aws_region_halts():
    print("[TEST 1] Testing that missing AWS_REGION halts 02 provisioning...")
    snippet = (
        "import os, importlib\n"
        "rcreator = importlib.import_module('scripts.02_gcp_aws_resource_creator')\n"
        "os.environ.pop('AWS_REGION', None)\n"
        "os.environ['GCP_BUCKET_LOCATION'] = 'asia-south1'\n"
        "try:\n"
        "    rcreator.validate_region_prerequisites()\n"
        "    print('FAILED_TO_CATCH')\n"
        "except ValueError as e:\n"
        "    assert 'AWS_REGION' in str(e)\n"
        "    print('SUCCESSFULLY_CAUGHT_MISSING_AWS_REGION')\n"
    )
    res = subprocess.run([sys.executable, "-c", snippet], cwd=project_root, capture_output=True, text=True)
    assert "SUCCESSFULLY_CAUGHT_MISSING_AWS_REGION" in res.stdout, f"Failed: {res.stdout}\n{res.stderr}"
    print("  [PASS] Missing AWS_REGION properly threw ValueError and halted.")

def test_missing_gcp_region_halts():
    print("\n[TEST 2] Testing that missing GCP_BUCKET_LOCATION halts 02 provisioning...")
    snippet = (
        "import os, importlib\n"
        "rcreator = importlib.import_module('scripts.02_gcp_aws_resource_creator')\n"
        "os.environ['AWS_REGION'] = 'ap-south-1'\n"
        "os.environ.pop('GCP_BUCKET_LOCATION', None)\n"
        "try:\n"
        "    rcreator.validate_region_prerequisites()\n"
        "    print('FAILED_TO_CATCH')\n"
        "except ValueError as e:\n"
        "    assert 'GCP_BUCKET_LOCATION' in str(e)\n"
        "    print('SUCCESSFULLY_CAUGHT_MISSING_GCP_REGION')\n"
    )
    res = subprocess.run([sys.executable, "-c", snippet], cwd=project_root, capture_output=True, text=True)
    assert "SUCCESSFULLY_CAUGHT_MISSING_GCP_REGION" in res.stdout, f"Failed: {res.stdout}\n{res.stderr}"
    print("  [PASS] Missing GCP_BUCKET_LOCATION properly threw ValueError and halted.")

def test_valid_regions_pass():
    print("\n[TEST 3] Testing that explicit regions pass validation cleanly...")
    snippet = (
        "import os, importlib\n"
        "rcreator = importlib.import_module('scripts.02_gcp_aws_resource_creator')\n"
        "os.environ['AWS_REGION'] = 'ap-south-1'\n"
        "os.environ['GCP_BUCKET_LOCATION'] = 'asia-south1'\n"
        "rcreator.validate_region_prerequisites()\n"
        "print('REGIONS_VALIDATION_PASSED')\n"
    )
    res = subprocess.run([sys.executable, "-c", snippet], cwd=project_root, capture_output=True, text=True)
    assert "REGIONS_VALIDATION_PASSED" in res.stdout, f"Failed: {res.stdout}\n{res.stderr}"
    print("  [PASS] Valid regions passed without exception.")

if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING STRICT REGION VALIDATION TESTS")
    print("=" * 80)
    test_missing_aws_region_halts()
    test_missing_gcp_region_halts()
    test_valid_regions_pass()
    print("\n" + "=" * 80)
    print(">>> ALL STRICT REGION VALIDATION TESTS PASSED!")
    print("=" * 80)
