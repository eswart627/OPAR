# scripts/ops/deprovision_gcp_drift_queue.py
# Purpose: Deprovisions / deletes GCP Pub/Sub topic and subscription used for drift detection.

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

env_path = os.path.join(project_root, "config", ".env")
load_dotenv(env_path, override=True)

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
GCP_KEY_PATH = os.environ.get("GCP_ADMIN_KEY_PATH")

TOPIC_NAME = f"projects/{GCP_PROJECT_ID}/topics/permission-rag-gcp-drift-topic" if GCP_PROJECT_ID else ""
SUB_NAME = f"projects/{GCP_PROJECT_ID}/subscriptions/permission-rag-gcp-drift-sub" if GCP_PROJECT_ID else ""


def deprovision_gcp_drift_queue():
    print("\n" + "=" * 70)
    print(">>> Deprovisioning GCP Pub/Sub Topic & Subscription...")
    print("=" * 70)

    if not (GCP_PROJECT_ID and GCP_KEY_PATH):
        print("[GCP][WARN] Missing GCP_PROJECT_ID or GCP_ADMIN_KEY_PATH in .env; skipping.")
        return

    # Resolve relative key path if needed
    resolved_key = GCP_KEY_PATH
    if not os.path.isabs(resolved_key):
        resolved_key = os.path.abspath(os.path.join(project_root, resolved_key))

    if not os.path.exists(resolved_key):
        print(f"[GCP][WARN] Key file not found at {resolved_key}; skipping Pub/Sub cleanup.")
        return

    try:
        from google.cloud import pubsub_v1
        from google.api_core import exceptions as gcp_exceptions
    except ImportError:
        print("[GCP][WARN] google-cloud-pubsub not installed; skipping Pub/Sub cleanup.")
        return

    publisher = pubsub_v1.PublisherClient.from_service_account_json(resolved_key)
    subscriber = pubsub_v1.SubscriberClient.from_service_account_json(resolved_key)

    # 1. Delete Subscription first
    try:
        subscriber.delete_subscription(subscription=SUB_NAME)
        print(f"[GCP][SUCCESS] Deleted Pub/Sub Subscription: {SUB_NAME}")
    except gcp_exceptions.NotFound:
        print(f"[GCP][INFO] Pub/Sub Subscription already gone: {SUB_NAME}")
    except Exception as e:
        print(f"[GCP][WARN] Could not delete subscription {SUB_NAME}: {e}")

    # 2. Delete Topic
    try:
        publisher.delete_topic(topic=TOPIC_NAME)
        print(f"[GCP][SUCCESS] Deleted Pub/Sub Topic: {TOPIC_NAME}")
    except gcp_exceptions.NotFound:
        print(f"[GCP][INFO] Pub/Sub Topic already gone: {TOPIC_NAME}")
    except Exception as e:
        print(f"[GCP][WARN] Could not delete topic {TOPIC_NAME}: {e}")

    print("=" * 70)
    print(">>> GCP Pub/Sub Drift Queue Deprovisioning Complete.")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    deprovision_gcp_drift_queue()
