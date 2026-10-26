# scripts/live_cloud_drift_listener.py
# Unified multi-cloud drift listener: Runs both AWS and GCP listeners concurrently
# in parallel threads, synchronizing all cloud drift events into the single SQLite metadata.db.

import sys
import threading
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(line_buffering=True)

from scripts.live_aws_drift_listener import start_listener as start_aws
from scripts.live_gcp_drift_listener import start_gcp_listener as start_gcp

def main():
    print("=" * 85)
    print("  UNIFIED MULTI-CLOUD STORAGE DRIFT LISTENER (AWS SQS + GCP PUB/SUB)")
    print("=" * 85)
    print("Starting dual background threads:")
    print("  Thread 1: AWS S3 Events (via SQS)")
    print("  Thread 2: GCP GCS Events (via Pub/Sub)")
    print("Both threads update the unified SQLite database: artifacts/metadata.db")
    print("=" * 85 + "\n")
    
    t_aws = threading.Thread(target=start_aws, kwargs={"single_event_mode": False}, daemon=True, name="AWS-Listener")
    t_gcp = threading.Thread(target=start_gcp, kwargs={"single_event_mode": False}, daemon=True, name="GCP-Listener")
    
    t_aws.start()
    t_gcp.start()
    
    try:
        t_aws.join()
        t_gcp.join()
    except KeyboardInterrupt:
        print("\n[STOPPED] Multi-cloud listener stopped by user.")

if __name__ == "__main__":
    main()
