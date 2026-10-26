# tests/test_pipeline_02_resource_creator.py
# Comprehensive Test Suite for Step 02: Resource & Identity Provisioner
# Bridges to: scripts/02_gcp_aws_resource_creator.py
# Combines: CLI parsing, drift queue isolation, static mode schema, and Auth0 federation dispatch.

import os
import sys
import json
import shutil
import subprocess
import importlib
from pathlib import Path
from unittest.mock import patch, MagicMock

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.env_loader import load_project_env
load_project_env()


def test_cli_parsing_and_drift_isolation():
    """Test 1: Verify CLI argument parsing and ensure drift queues are decoupled from main()."""
    print("\n[TEST 1] Testing CLI Argument Parsing and Drift Isolation...")
    cmd = [sys.executable, "scripts/02_gcp_aws_resource_creator.py", "--help"]
    result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    assert "--auth_mode" in result.stdout

    # Verify drift decoupling
    with open(os.path.join(project_root, "scripts", "02_gcp_aws_resource_creator.py"), "r", encoding="utf-8") as f:
        code = f.read()
    main_block = code[code.find("def main():"):]
    assert "setup_drift_queues()" not in main_block, "setup_drift_queues() must NOT be called in main() of 02!"
    print("  [PASS] CLI parsing and drift queue isolation verified.")


def test_static_credentials_schema():
    """Test 2: Verify static mode credential structures and CredentialManager compatibility."""
    print("\n[TEST 2] Testing Static Mode Credential Schema...")
    creds_path = os.path.join(project_root, "artifacts", "test_credential.txt")
    if not os.path.exists(creds_path):
        # Create a mock static credentials file for testing
        mock_creds = [
            {"name": "user-1", "aws_access_key": "AKIA_TEST", "aws_secret_key": "SEC_TEST", "gcp_credential": {}},
            {"name": "baseline-admin", "aws_access_key": "AKIA_ADMIN", "aws_secret_key": "SEC_ADMIN", "gcp_credential": {}}
        ]
        os.makedirs(os.path.dirname(creds_path), exist_ok=True)
        with open(creds_path, "w", encoding="utf-8") as f:
            json.dump(mock_creds, f)

    from src.managers.credential_manager import CredentialManager
    os.environ["AUTH_MODE"] = "static"
    cm = CredentialManager()
    assert "user-1" in cm.users
    assert "baseline-admin" in cm.users
    print("  [PASS] Static credentials schema and CredentialManager compatibility verified.")


def test_auth0_federated_provisioning():
    """Test 3: Verify Auth0 mode provisioning, federated roster generation, and zero static keys."""
    print("\n[TEST 3] Testing Auth0 Mode Federation and Dynamic STS Dispatch...")
    rcreator = importlib.import_module("scripts.02_gcp_aws_resource_creator")
    creds_path = os.path.join(project_root, "artifacts", "test_credential.txt")
    backup_path = creds_path + ".bak"

    if os.path.exists(creds_path):
        shutil.copy2(creds_path, backup_path)

    try:
        with patch.object(rcreator, "create_gcp_buckets") as mock_gcp_b, \
             patch.object(rcreator, "create_aws_buckets") as mock_aws_b, \
             patch("scripts.ops.identity.auth0_create_user.create_all_blueprint_users") as mock_auth0_sync, \
             patch("scripts.ops.identity.setup_aws_idp.setup_aws_idp") as mock_idp:

            rcreator.setup_auth0()

            mock_gcp_b.assert_called_once()
            mock_aws_b.assert_called_once()
            mock_auth0_sync.assert_called_once()
            mock_idp.assert_called_once()

            assert os.path.exists(creds_path), "test_credential.txt was not created in Auth0 mode"
            with open(creds_path, "r", encoding="utf-8") as f:
                roster = json.load(f)

            for u in roster:
                assert u.get("auth_mode") == "auth0"
                assert u.get("status") == "federated"
                assert u.get("aws_access_key") == "", "Static AWS access key must be empty in Auth0 mode!"

            # Test CredentialManager dispatch in Auth0 mode
            from src.managers.credential_manager import CredentialManager
            os.environ["AUTH_MODE"] = "auth0"
            with patch.object(CredentialManager, "_load_auth0_federated_users") as mock_load_fed:
                cm = CredentialManager()
                mock_load_fed.assert_called_once()

        print("  [PASS] Auth0 federated provisioning and dynamic STS dispatch verified.")
    finally:
        if os.path.exists(backup_path):
            shutil.move(backup_path, creds_path)


if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING PIPELINE 02 RESOURCE CREATOR CONSOLIDATED TEST SUITE")
    print("=" * 80)
    test_cli_parsing_and_drift_isolation()
    test_static_credentials_schema()
    test_auth0_federated_provisioning()
    print("\n" + "=" * 80)
    print(">>> ALL PIPELINE 02 TESTS PASSED!")
    print("=" * 80)
