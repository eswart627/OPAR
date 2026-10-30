# web/services/drift_control_service.py
# Thread-safe service managing cloud drift queues (SQS & Pub/Sub),
# background listener daemons, and circular in-memory log streaming.

import os
import sys
import time
import signal
import threading
import subprocess
from collections import deque
from typing import Dict, Any, List, Optional
from pathlib import Path

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.env_loader import load_project_env
load_project_env()


class DriftControlService:
    _instance: Optional["DriftControlService"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.listener_process: Optional[subprocess.Popen] = None
        self.listener_provider: str = "none"
        self.listener_thread: Optional[threading.Thread] = None
        self.log_buffer: deque = deque(maxlen=300)
        self._log_counter: int = 0
        self.operation_lock = threading.Lock()
        self.is_busy: bool = False
        self.busy_action: str = ""
        self._cached_queue_status: Optional[Dict[str, Any]] = None
        self._cached_queue_time: float = 0.0

        # Pre-seed system greeting log
        self._add_log("SYSTEM", "Drift Control Service initialized. Ready for operations.")

    @classmethod
    def get_instance(cls) -> "DriftControlService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _add_log(self, source: str, message: str) -> None:
        self._log_counter += 1
        timestamp = time.strftime("%H:%M:%S")
        entry = {
            "id": self._log_counter,
            "timestamp": timestamp,
            "source": source,
            "message": message,
            "raw": f"[{timestamp}] [{source}] {message}"
        }
        self.log_buffer.append(entry)

    # -------------------------------------------------------------------------
    # Queue Status Inspection
    # -------------------------------------------------------------------------
    def inspect_queue_status(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Queries AWS SQS and GCP Pub/Sub to check if drift queues currently exist."""
        now = time.time()
        if not force_refresh and self._cached_queue_status and (now - self._cached_queue_time < 15.0):
            return self._cached_queue_status

        aws_deployed = False
        aws_queue_url = None
        gcp_deployed = False
        gcp_sub_name = None

        # Check AWS SQS (with 1.0s fast timeout)
        try:
            import boto3
            from botocore.config import Config
            fast_cfg = Config(connect_timeout=1.0, read_timeout=1.0, retries={"max_attempts": 0})
            aws_region = os.environ.get("AWS_REGION", "ap-south-1")
            access_key = os.environ.get("AWS_ADMIN_ACCESS_KEY")
            secret_key = os.environ.get("AWS_ADMIN_SECRET_KEY")
            if access_key and secret_key:
                sqs = boto3.client(
                    "sqs",
                    region_name=aws_region,
                    aws_access_key_id=access_key,
                    aws_secret_access_key=secret_key,
                    config=fast_cfg,
                )
                resp = sqs.get_queue_url(QueueName="permission-rag-drift-queue")
                aws_queue_url = resp.get("QueueUrl")
                aws_deployed = bool(aws_queue_url)
        except Exception:
            aws_deployed = False

        # Check GCP Pub/Sub (with 1.0s fast timeout)
        try:
            from google.cloud import pubsub_v1
            gcp_key = os.environ.get("GCP_ADMIN_KEY_PATH")
            project_id = os.environ.get("GCP_PROJECT_ID")
            if gcp_key and os.path.exists(gcp_key) and project_id:
                sub_client = pubsub_v1.SubscriberClient.from_service_account_json(gcp_key)
                sub_path = f"projects/{project_id}/subscriptions/permission-rag-gcp-drift-sub"
                try:
                    sub_client.get_subscription(request={"subscription": sub_path}, timeout=1.0)
                    gcp_deployed = True
                    gcp_sub_name = sub_path
                except Exception:
                    gcp_deployed = False
        except Exception:
            gcp_deployed = False

        status = {
            "aws": {
                "deployed": aws_deployed,
                "queue_name": "permission-rag-drift-queue",
                "queue_url": aws_queue_url,
            },
            "gcp": {
                "deployed": gcp_deployed,
                "subscription_name": "permission-rag-gcp-drift-sub",
                "subscription_path": gcp_sub_name,
            },
            "all_deployed": aws_deployed and gcp_deployed,
            "any_deployed": aws_deployed or gcp_deployed,
        }
        self._cached_queue_status = status
        self._cached_queue_time = now
        return status

    # -------------------------------------------------------------------------
    # Queue Provisioning / Teardown
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # Queue Provisioning / Teardown (Subprocess-Isolated)
    # -------------------------------------------------------------------------
    def _run_script(self, script_name: str, source_tag: str) -> Dict[str, Any]:
        """Runs a frozen ops script in an isolated subprocess, streaming its stdout/stderr."""
        script_path = os.path.join(BASE_DIR, "scripts", "ops", "drift", script_name)
        if not os.path.exists(script_path):
            self._add_log(f"{source_tag}_ERR", f"Script not found: {script_path}")
            return {"status": "error", "message": f"Script not found: {script_path}"}

        try:
            res = subprocess.run(
                [sys.executable, "-u", script_path],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=45,
            )
            stdout = res.stdout or ""
            stderr = res.stderr or ""

            # Stream meaningful stdout lines into the circular log buffer (skipping pure ASCII dividers)
            for line in stdout.splitlines():
                clean = line.strip()
                if clean and not all(c in "=-_*~# " for c in clean):
                    self._add_log(source_tag, clean)
            for line in stderr.splitlines():
                clean = line.strip()
                if clean and not all(c in "=-_*~# " for c in clean):
                    self._add_log(f"{source_tag}_ERR", clean)

            if res.returncode == 0:
                return {"status": "success", "output": stdout}
            else:
                return {"status": "error", "code": res.returncode, "error": stderr or stdout}
        except subprocess.TimeoutExpired:
            self._add_log(f"{source_tag}_ERR", "Operation timed out after 45s.")
            return {"status": "error", "message": "Timed out after 45s"}
        except Exception as e:
            self._add_log(f"{source_tag}_ERR", f"Subprocess error: {str(e)}")
            return {"status": "error", "message": str(e)}

    def setup_queues(self, provider: str = "all") -> Dict[str, Any]:
        """Provisions AWS SQS and/or GCP Pub/Sub drift queues and attaches event notifications."""
        self._cached_queue_status = None
        results = {}
        self._add_log("SYSTEM", f"Starting drift queue setup (provider: {provider.upper()})...")

        if provider in ("all", "aws"):
            self._add_log("AWS", "Creating SQS queue and attaching S3 event notifications...")
            out = self._run_script("setup_aws_drift_queue.py", "AWS")
            results["aws"] = out["status"]
            if out["status"] == "success":
                self._add_log("AWS", "SQS drift queue provisioned successfully.")
            else:
                self._add_log("AWS_ERR", "AWS SQS provisioning completed with warnings/errors.")

        if provider in ("all", "gcp"):
            self._add_log("GCP", "Creating Pub/Sub topic & subscription and attaching GCS notifications...")
            out = self._run_script("setup_gcp_drift_queue.py", "GCP")
            results["gcp"] = out["status"]
            if out["status"] == "success":
                self._add_log("GCP", "Pub/Sub drift subscription provisioned successfully.")
            else:
                self._add_log("GCP_ERR", "GCP Pub/Sub provisioning completed with warnings/errors.")

        return results

    def teardown_queues(self, provider: str = "all") -> Dict[str, Any]:
        """Detaches event notifications and deletes AWS SQS and/or GCP Pub/Sub drift queues."""
        self._cached_queue_status = None
        results = {}
        self._add_log("SYSTEM", f"Starting drift queue teardown (provider: {provider.upper()})...")

        if provider in ("all", "aws"):
            self._add_log("AWS", "Detaching S3 event notifications and deleting SQS queue...")
            out = self._run_script("deprovision_aws_drift_queue.py", "AWS")
            results["aws"] = out["status"]
            if out["status"] == "success":
                self._add_log("AWS", "SQS drift queue deleted successfully.")
            else:
                self._add_log("AWS_ERR", "AWS SQS teardown completed with warnings/errors.")

        if provider in ("all", "gcp"):
            self._add_log("GCP", "Deleting Pub/Sub topic and subscription...")
            out = self._run_script("deprovision_gcp_drift_queue.py", "GCP")
            results["gcp"] = out["status"]
            if out["status"] == "success":
                self._add_log("GCP", "Pub/Sub drift subscription deleted successfully.")
            else:
                self._add_log("GCP_ERR", "GCP Pub/Sub teardown completed with warnings/errors.")

        return results

    # -------------------------------------------------------------------------
    # Listener Daemon Management
    # -------------------------------------------------------------------------
    def is_listener_running(self) -> bool:
        if self.listener_process is None:
            return False
        return self.listener_process.poll() is None

    def start_listener(self, provider: str = "all") -> Dict[str, Any]:
        """Starts the background listener daemon process and pipes stdout to the log buffer."""
        if self.is_listener_running():
            return {
                "status": "already_running",
                "pid": self.listener_process.pid,
                "provider": self.listener_provider,
            }

        # Select target script
        if provider == "aws":
            script_path = os.path.join(BASE_DIR, "scripts", "ops", "drift", "live_aws_drift_listener.py")
        elif provider == "gcp":
            script_path = os.path.join(BASE_DIR, "scripts", "ops", "drift", "live_gcp_drift_listener.py")
        else:
            script_path = os.path.join(BASE_DIR, "scripts", "ops", "drift", "gcp_aws_drift_listener.py")

        if not os.path.exists(script_path):
            return {"status": "error", "message": f"Script not found: {script_path}"}

        # Ensure SQLite metadata.db exists so MetadataStore.load() connects cleanly
        db_path = os.path.join(BASE_DIR, "artifacts", "metadata.db")
        if not os.path.exists(db_path):
            try:
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                import sqlite3
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    global_uuid TEXT PRIMARY KEY,
                    file_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    region TEXT,
                    endpoint TEXT,
                    raw_json TEXT NOT NULL
                );
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_file_name ON metadata(file_name);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_bucket ON metadata(bucket);")
                conn.commit()
                conn.close()
                self._append_log("SYSTEM", f"Initialized SQLite metadata database at {db_path}")
            except Exception as e:
                self._append_log("SYSTEM", f"Warning: Could not pre-initialize metadata.db: {e}")

        # Spawn daemon process with unbuffered output
        cmd = [sys.executable, "-u", script_path]
        try:
            self.listener_process = subprocess.Popen(
                cmd,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self.listener_provider = provider
            pid = self.listener_process.pid
            self._add_log("SYSTEM", f"Drift listener daemon started (PID: {pid}, Mode: {provider.upper()})")

            # Start reader thread to ingest logs continuously
            def _reader_loop(proc):
                for line in iter(proc.stdout.readline, ""):
                    if line:
                        cleaned = line.rstrip()
                        if cleaned and not all(c in "=-_*~# " for c in cleaned):
                            # Extract tag if present (e.g. [AWS], [GCP], [DRIFT])
                            source = "DAEMON"
                            if cleaned.startswith("[") and "]" in cleaned:
                                source = cleaned[1:cleaned.index("]")]
                                msg = cleaned[cleaned.index("]") + 1:].strip()
                            else:
                                msg = cleaned
                            self._add_log(source, msg)
                proc.stdout.close()

            self.listener_thread = threading.Thread(target=_reader_loop, args=(self.listener_process,), daemon=True)
            self.listener_thread.start()

            return {"status": "started", "pid": pid, "provider": provider}
        except Exception as e:
            self._add_log("ERROR", f"Failed to spawn listener process: {str(e)}")
            return {"status": "error", "message": str(e)}

    def stop_listener(self) -> Dict[str, Any]:
        """Stops the live listener daemon process gracefully."""
        if not self.is_listener_running():
            return {"status": "not_running"}

        try:
            pid = self.listener_process.pid
            self.listener_process.terminate()
            try:
                self.listener_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.listener_process.kill()
                self.listener_process.wait(timeout=2)

            self._add_log("SYSTEM", f"Drift listener daemon stopped (PID: {pid})")
            self.listener_process = None
            self.listener_provider = "none"
            return {"status": "stopped", "pid": pid}
        except Exception as e:
            self._add_log("ERROR", f"Error stopping listener: {str(e)}")
            return {"status": "error", "message": str(e)}

    # -------------------------------------------------------------------------
    # Logs Retrieval
    # -------------------------------------------------------------------------
    def get_logs(self, since_id: int = 0) -> List[Dict[str, Any]]:
        with self.operation_lock:
            if since_id <= 0:
                return list(self.log_buffer)
            return [entry for entry in self.log_buffer if entry.get("id", 0) > since_id]

    def clear_logs(self) -> int:
        with self.operation_lock:
            self.log_buffer.clear()
            self._log_counter += 1
            entry = {
                "id": self._log_counter,
                "timestamp": time.strftime("%H:%M:%S"),
                "source": "SYSTEM",
                "message": "Log buffer cleared.",
                "raw": f"[{time.strftime('%H:%M:%S')}] [SYSTEM] Log buffer cleared."
            }
            self.log_buffer.append(entry)
            return self._log_counter
