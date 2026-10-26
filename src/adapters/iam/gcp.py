from typing import Dict, Any
import os
from google.cloud import storage
from google.api_core.exceptions import Forbidden, NotFound
from .base import AccessControlBase

class GCPAccessControl(AccessControlBase):
    def __init__(self, user_credential: Dict[str, Any]):
        super().__init__(user_credential)
        self.gcp_client = self._create_gcp_client()

    def _create_gcp_client(self):
        cred = self.user_credential.get("gcp_credential")
        if not cred:
            return None
        # 1. OAuth / Federated token support (Auth0 / STS)
        if isinstance(cred, dict) and ("token" in cred or "access_token" in cred):
            try:
                from google.oauth2 import credentials as g_credentials
                token_val = cred.get("token") or cred.get("access_token")
                gcreds = g_credentials.Credentials(token=token_val)
                project = cred.get("project_id") or os.environ.get("GCP_PROJECT_ID", "project-444c1d0a-6028-4d76-8d5")
                return storage.Client(credentials=gcreds, project=project)
            except Exception as e:
                print(f"[GCP][ERROR] Failed to create storage client from OAuth token: {e}")
        # 2. Legacy service account json dict
        if isinstance(cred, dict) and "private_key" in cred:
            try:
                return storage.Client.from_service_account_info(cred)
            except Exception as e:
                print(f"[GCP][ERROR] Failed to create storage client from key info: {e}")
        # 3. Fall back to ADC client
        try:
            project = (self.user_credential.get("gcp_credential") or {}).get("project_id") or os.environ.get("GCP_PROJECT_ID", "project-444c1d0a-6028-4d76-8d5")
            return storage.Client(project=project)
        except Exception as e:
            print(f"[GCP][ERROR] Failed to create default storage client: {e}")
            return None

    def check_access(self, resource_metadata: Dict[str, Any]) -> bool:
        bucket = (resource_metadata.get("parameters") or {}).get("bucket")
        name   = resource_metadata.get("file_name")
        if not (bucket and name):
            return False

        cred = self.user_credential.get("gcp_credential") or {}
        # If allowed_buckets mapping is present (RBAC mapping)
        if isinstance(cred, dict) and "allowed_buckets" in cred:
            if cred.get("full_access"):
                return True
            return bucket in cred.get("allowed_buckets", [])

        # If dedicated service account client is available
        if not self.gcp_client:
            return False
        try:
            blob = self.gcp_client.bucket(bucket).blob(name)
            needed  = ['storage.objects.get']
            allowed = blob.test_iam_permissions(needed)
            if needed[0] not in allowed:
                return False
            return blob.exists()
        except (Forbidden, NotFound):
            return False
        except Exception:
            return False
