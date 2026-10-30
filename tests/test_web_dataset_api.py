# tests/test_web_dataset_api.py
# Unit Test Suite for Dataset & Ingestion APIs
# Validates:
#   1. GET /api/dataset/storage-summary schema and bucket statistics
#   2. POST /api/dataset/upload-dataset input validation

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

os.environ["AUTH_MODE"] = "static"

from src.env_loader import load_project_env
load_project_env()

from web.app import create_app
from src.managers.credential_manager import CredentialManager


class TestWebDatasetAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(CredentialManager, "_load_auth0_federated_users"):
            cls.app = create_app()
            cls.client = cls.app.test_client()

    def test_01_get_storage_summary(self):
        """Test GET /api/dataset/storage-summary."""
        print("\n[TEST 1] Testing GET /api/dataset/storage-summary...")
        res = self.client.get("/api/dataset/storage-summary")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("aws", data)
        self.assertIn("gcp", data)
        self.assertIn("buckets", data["aws"])
        self.assertIn("buckets", data["gcp"])
        print(f"  [PASS] Storage summary verified: {len(data['aws']['buckets'])} AWS buckets, {len(data['gcp']['buckets'])} GCP buckets.")

    def test_02_upload_dataset_validation(self):
        """Test POST /api/dataset/upload-dataset validation."""
        print("\n[TEST 2] Testing upload validation...")
        # Missing file
        res = self.client.post("/api/dataset/upload-dataset")
        self.assertEqual(res.status_code, 400)
        print("  [PASS] Missing file rejected with 400.")

    @patch("web.routes.dataset_routes.subprocess.run")
    def test_03_teardown_dispatch(self, mock_sub):
        """Test POST /api/dataset/teardown dispatch."""
        print("\n[TEST 3] Testing POST /api/dataset/teardown dispatch...")
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "Teardown sequence complete."
        mock_sub.return_value = mock_res

        res = self.client.post("/api/dataset/teardown")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])
        mock_sub.assert_called_once()
        print("  [PASS] Teardown endpoint dispatches sequence runner cleanly.")


if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING WEB DATASET API TEST SUITE")
    print("=" * 80)
    unittest.main()
