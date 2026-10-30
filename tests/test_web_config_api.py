# tests/test_web_config_api.py
# Comprehensive Test Suite for Web App Configuration & Operations APIs
# Bridges to: web/routes/config_routes.py & web/services/drift_control_service.py
# Validates:
#   1. GET /api/config/state schema and default values
#   2. POST /api/config/cache dynamic toggle and status reflection
#   3. POST /api/config/idp-mode switching between 'static' and 'auth0'
#   4. GET /api/config/logs and POST /api/config/logs/clear circular buffer behavior
#   5. POST /api/config/drift-queue and POST /api/config/listener mock verification

import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Set static mode for offline unit test
os.environ["AUTH_MODE"] = "static"

from src.env_loader import load_project_env
load_project_env()

from web.app import create_app
from web.services.drift_control_service import DriftControlService
from web.services.pipeline_service import PipelineService
from src.managers.credential_manager import CredentialManager


class TestWebConfigAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Prevent live cloud calls during offline unit testing
        with patch.object(CredentialManager, "_load_auth0_federated_users"):
            cls.app = create_app()
            cls.client = cls.app.test_client()

    @patch.object(DriftControlService, "inspect_queue_status")
    def test_01_get_config_state(self, mock_inspect):
        """Test 1: Verify GET /api/config/state schema."""
        print("\n[TEST 1] Testing GET /api/config/state endpoint...")
        mock_inspect.return_value = {
            "aws": {"deployed": False, "queue_name": "permission-rag-drift-queue", "queue_url": None},
            "gcp": {"deployed": False, "subscription_name": "permission-rag-gcp-drift-sub", "subscription_path": None},
            "all_deployed": False,
            "any_deployed": False,
        }
        response = self.client.get("/api/config/state")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data.get("status"), "success")
        self.assertIn("auth_mode", data)
        self.assertIn("cache", data)
        self.assertIn("enabled", data["cache"])
        self.assertIn("stats", data["cache"])
        self.assertIn("queues", data)
        self.assertIn("aws", data["queues"])
        self.assertIn("gcp", data["queues"])
        self.assertIn("listener", data)
        self.assertIn("running", data["listener"])
        print(f"  [PASS] State schema verified. Auth Mode: {data['auth_mode']}, Cache: {data['cache']['enabled']}")

    @patch.object(DriftControlService, "inspect_queue_status")
    def test_02_toggle_cache(self, mock_inspect):
        """Test 2: Verify POST /api/config/cache dynamic toggle."""
        print("\n[TEST 2] Testing POST /api/config/cache dynamic toggle...")
        mock_inspect.return_value = {"aws": {"deployed": False}, "gcp": {"deployed": False}}

        # Enable cache
        res_on = self.client.post("/api/config/cache", json={"enabled": True})
        self.assertEqual(res_on.status_code, 200)
        self.assertTrue(res_on.get_json()["cache_enabled"])

        state_on = self.client.get("/api/config/state").get_json()
        self.assertTrue(state_on["cache"]["enabled"])

        # Disable cache
        res_off = self.client.post("/api/config/cache", json={"enabled": False})
        self.assertEqual(res_off.status_code, 200)
        self.assertFalse(res_off.get_json()["cache_enabled"])

        state_off = self.client.get("/api/config/state").get_json()
        self.assertFalse(state_off["cache"]["enabled"])
        print("  [PASS] Cache successfully toggled ON and OFF dynamically.")

    @patch.object(CredentialManager, "_load_auth0_federated_users")
    def test_03_switch_idp_mode(self, mock_fed):
        """Test 3: Verify POST /api/config/idp-mode."""
        print("\n[TEST 3] Testing POST /api/config/idp-mode...")
        # Switch to auth0
        res = self.client.post("/api/config/idp-mode", json={"mode": "auth0"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["auth_mode"], "auth0")
        self.assertEqual(os.environ.get("AUTH_MODE"), "auth0")

        # Switch back to static
        res2 = self.client.post("/api/config/idp-mode", json={"mode": "static"})
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.get_json()["auth_mode"], "static")
        self.assertEqual(os.environ.get("AUTH_MODE"), "static")

        # Invalid mode
        res_bad = self.client.post("/api/config/idp-mode", json={"mode": "invalid_mode"})
        self.assertEqual(res_bad.status_code, 400)
        print("  [PASS] IdP Mode switching and validation verified.")

    def test_04_listener_logs_buffer(self):
        """Test 4: Verify GET /api/config/logs and buffer clearing."""
        print("\n[TEST 4] Testing GET /api/config/logs and buffer clearing...")
        # Add test log via service
        service = DriftControlService.get_instance()
        service._add_log("TEST_UNIT", "Unit test message for buffer")

        res = self.client.get("/api/config/logs")
        self.assertEqual(res.status_code, 200)
        logs = res.get_json()["logs"]
        self.assertGreater(len(logs), 0)
        found = any("Unit test message for buffer" in l["message"] for l in logs)
        self.assertTrue(found)

        # Clear logs
        res_clear = self.client.post("/api/config/logs/clear")
        self.assertEqual(res_clear.status_code, 200)
        logs_after = self.client.get("/api/config/logs").get_json()["logs"]
        self.assertEqual(len(logs_after), 1)
        print("  [PASS] Log streaming buffer and clear action verified.")

    @patch.object(DriftControlService, "inspect_queue_status")
    @patch.object(DriftControlService, "setup_queues")
    @patch.object(DriftControlService, "teardown_queues")
    def test_05_drift_queue_toggle(self, mock_teardown, mock_setup, mock_inspect):
        """Test 5: Verify POST /api/config/drift-queue dispatch."""
        print("\n[TEST 5] Testing POST /api/config/drift-queue dispatch...")
        mock_inspect.return_value = {"aws": {"deployed": True}, "gcp": {"deployed": True}}
        mock_setup.return_value = {"aws": "success", "gcp": "success"}
        mock_teardown.return_value = {"aws": "success", "gcp": "success"}

        res_setup = self.client.post("/api/config/drift-queue", json={"action": "setup", "provider": "all"})
        self.assertEqual(res_setup.status_code, 200)
        mock_setup.assert_called_once_with(provider="all")

        res_teardown = self.client.post("/api/config/drift-queue", json={"action": "teardown", "provider": "aws"})
        self.assertEqual(res_teardown.status_code, 200)
        mock_teardown.assert_called_once_with(provider="aws")
        print("  [PASS] Drift queue API endpoints correctly dispatch to service actions.")

    @patch.object(DriftControlService, "start_listener")
    @patch.object(DriftControlService, "stop_listener")
    def test_06_listener_toggle(self, mock_stop, mock_start):
        """Test 6: Verify POST /api/config/listener start and stop dispatch."""
        print("\n[TEST 6] Testing POST /api/config/listener dispatch...")
        mock_start.return_value = {"status": "started", "pid": 9999, "provider": "all"}
        mock_stop.return_value = {"status": "stopped", "pid": 9999}

        res_start = self.client.post("/api/config/listener", json={"action": "start", "provider": "all"})
        self.assertEqual(res_start.status_code, 200)
        mock_start.assert_called_once_with(provider="all")

        res_stop = self.client.post("/api/config/listener", json={"action": "stop"})
        self.assertEqual(res_stop.status_code, 200)
        mock_stop.assert_called_once()
        print("  [PASS] Listener toggle API correctly dispatches to start/stop handlers.")

    def test_07_cache_invalidation(self):
        """Test 7: Verify cache flush and single-user eviction."""
        print("\n[TEST 7] Testing POST /api/config/cache/invalidate endpoints...")
        # Flush all
        res_flush = self.client.post("/api/config/cache/invalidate", json={"all": True})
        self.assertEqual(res_flush.status_code, 200)
        self.assertEqual(res_flush.get_json()["status"], "success")

        # Evict specific user
        res_user = self.client.post("/api/config/cache/invalidate", json={"user": "user-1"})
        self.assertEqual(res_user.status_code, 200)
        self.assertEqual(res_user.get_json()["user"], "user-1")
        print("  [PASS] Cache invalidation (flush all and evict user) verified.")


if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING WEB CONFIG API TEST SUITE")
    print("=" * 80)
    unittest.main()
