# scripts/ops/setup_gcp_drift_queue.py
# Sets up GCP Pub/Sub topic & subscription, and attaches GCS bucket storage notifications.
# Includes pre-flight validation and actionable error guidance for disabled APIs or missing IAM roles.

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

env_path = os.path.join(project_root, "config", ".env")
load_dotenv(env_path, override=True)


def validate_gcp_prerequisites():
    """
    Validates all local files, credentials, and environment prerequisites.
    Returns: (project_id, key_path, access_model_path) or exits with code 1.
    """
    errors = []

    project_id = os.environ.get("GCP_PROJECT_ID")
    key_path = os.environ.get("GCP_ADMIN_KEY_PATH")
    bucket_location = os.environ.get("GCP_BUCKET_LOCATION")

    if not project_id:
        errors.append("  - GCP_PROJECT_ID is missing from config/.env")

    if not bucket_location:
        errors.append(
            "  - GCP_BUCKET_LOCATION is missing from config/.env.\n"
            "    GCP Pub/Sub notifications strictly require an explicit GCP_BUCKET_LOCATION\n"
            "    (e.g. GCP_BUCKET_LOCATION=asia-south1). Fallback defaults are disallowed."
        )

    if not key_path:
        errors.append("  - GCP_ADMIN_KEY_PATH is missing from config/.env")
    else:
        # Resolve relative to project root if not absolute
        if not os.path.isabs(key_path):
            key_path = os.path.abspath(os.path.join(project_root, key_path))
        if not os.path.exists(key_path):
            errors.append(
                f"  - GCP Service Account JSON key file not found on disk at:\n"
                f"    {key_path}\n"
                f"    Please place your service account JSON file there or update GCP_ADMIN_KEY_PATH."
            )

    access_model_path = os.path.join(project_root, "artifacts", "generated_access_model.json")
    if not os.path.exists(access_model_path):
        errors.append(
            f"  - Generated access model not found at:\n"
            f"    {access_model_path}\n"
            f"    Please run 'python scripts/02_create_resources.py' first to provision the test buckets."
        )

    if errors:
        print("\n" + "=" * 80)
        print("  [GCP PRE-FLIGHT ERROR] MISSING PREREQUISITES")
        print("=" * 80)
        for err in errors:
            print(err)
        print("\nHOW TO FIX:")
        print(f"  1. Open or create: {env_path}")
        print("  2. Ensure the following keys are defined:")
        print("       GCP_PROJECT_ID=your-gcp-project-id")
        print("       GCP_ADMIN_KEY_PATH=config/gcp_admin.json")
        print("=" * 80 + "\n")
        sys.exit(1)

    return project_id, key_path, access_model_path


def setup_gcp_pubsub():
    project_id, key_path, access_model_path = validate_gcp_prerequisites()

    topic_name = f"projects/{project_id}/topics/permission-rag-gcp-drift-topic"
    sub_name = f"projects/{project_id}/subscriptions/permission-rag-gcp-drift-sub"

    print("=" * 80)
    print("  SETTING UP GCP PUB/SUB QUEUE & GCS NOTIFICATIONS FOR DRIFT DETECTION")
    print("=" * 80)
    print(f"[GCP][INFO] Target Project: {project_id}")
    print(f"[GCP][INFO] Admin Key Path: {key_path}")

    try:
        from google.cloud import storage
        from google.cloud import pubsub_v1
        from google.api_core import exceptions as gcp_exceptions
    except ImportError:
        print("\n[ERROR] Google Cloud SDK libraries not installed. Please run:")
        print("  pip install google-cloud-storage google-cloud-pubsub")
        sys.exit(1)

    # Initialize GCP clients
    try:
        publisher = pubsub_v1.PublisherClient.from_service_account_json(key_path)
        subscriber = pubsub_v1.SubscriberClient.from_service_account_json(key_path)
        gcp_storage = storage.Client.from_service_account_json(key_path)
    except Exception as e:
        print(f"\n[GCP][ERROR] Failed to initialize Google Cloud clients with key '{key_path}': {e}")
        sys.exit(1)

    # Read service account email for helpful messages
    sa_email = "your-service-account"
    try:
        with open(key_path, "r", encoding="utf-8") as f:
            sa_data = json.load(f)
            sa_email = sa_data.get("client_email", "your-service-account")
    except Exception:
        pass

    # Helper function for friendly error handling
    def handle_gcp_api_error(action_name: str, err: Exception):
        err_str = str(err)
        print("\n" + "=" * 80)
        
        # 1. API Disabled Check
        if "SERVICE_DISABLED" in err_str or "has not been used in project" in err_str:
            print(f"  [GCP API ERROR] Cloud Pub/Sub API is NOT enabled for project '{project_id}'!")
            print("=" * 80)
            print(f"Action '{action_name}' failed because Google Cloud Pub/Sub API is disabled.")
            print("\nHOW TO FIX (1-Click URL):")
            print(f"  1. Open this direct link in your browser:")
            print(f"     https://console.cloud.google.com/apis/library/pubsub.googleapis.com?project={project_id}")
            print(f"  2. Click the blue 'ENABLE' button.")
            print(f"  3. Wait 10 seconds and re-run this script.")
            print("=" * 80 + "\n")
            sys.exit(1)

        # 2. Permission Denied Check
        elif "PERMISSION_DENIED" in err_str or "User not authorized" in err_str or "403" in err_str:
            print(f"  [GCP IAM ERROR] Service Account lacks Pub/Sub permissions!")
            print("=" * 80)
            print(f"Service Account: {sa_email}")
            print(f"Missing Role:    Pub/Sub Admin (roles/pubsub.admin)")
            print("\nHOW TO FIX:")
            print(f"  1. Open the GCP IAM Console:")
            print(f"     https://console.cloud.google.com/iam-admin/iam?project={project_id}")
            print(f"  2. Find '{sa_email}' in the list of principals.")
            print(f"  3. Click the pencil icon ('Edit principal') on the right.")
            print(f"  4. Click '+ ADD ANOTHER ROLE'.")
            print(f"  5. Search for and select: 'Pub/Sub Admin' (roles/pubsub.admin).")
            print(f"  6. Click 'SAVE' and re-run this script.")
            print("=" * 80 + "\n")
            sys.exit(1)

        else:
            print(f"  [GCP ERROR] Failed during {action_name}: {err}")
            print("=" * 80 + "\n")
            sys.exit(1)

    # 1. Create or get Topic
    print(f"\n[1] Creating or retrieving GCP Pub/Sub Topic: {topic_name}...")
    try:
        publisher.create_topic(name=topic_name)
        print(f"    -> [SUCCESS] Created Topic: {topic_name}")
    except Exception as e:
        if "AlreadyExists" in str(e) or "409" in str(e):
            print(f"    -> [INFO] Topic already exists: {topic_name}")
        else:
            handle_gcp_api_error("Create Pub/Sub Topic", e)

    # 2. Create or get Pull Subscription (the Queue)
    print(f"\n[2] Creating or retrieving GCP Pub/Sub Pull Subscription: {sub_name}...")
    try:
        subscriber.create_subscription(
            name=sub_name,
            topic=topic_name,
            ack_deadline_seconds=20,
        )
        print(f"    -> [SUCCESS] Created Subscription: {sub_name}")
    except Exception as e:
        if "AlreadyExists" in str(e) or "409" in str(e):
            print(f"    -> [INFO] Subscription already exists: {sub_name}")
        else:
            handle_gcp_api_error("Create Pub/Sub Subscription", e)

    # 3. Grant GCS Service Agent permission to publish to this topic
    print(f"\n[3] Granting GCS service agent publish permissions to topic...")
    try:
        gcs_service_account = gcp_storage.get_service_account_email()
        print(f"    -> GCS Service Agent: {gcs_service_account}")
        policy = publisher.get_iam_policy(request={"resource": topic_name})
        policy.bindings.add(
            role="roles/pubsub.publisher",
            members=[f"serviceAccount:{gcs_service_account}"]
        )
        publisher.set_iam_policy(request={"resource": topic_name, "policy": policy})
        print("    -> [SUCCESS] GCS permission granted on Pub/Sub topic.")
    except Exception as e:
        print(f"    -> [WARN] IAM set warning (may already be granted): {e}")

    # 4. Attach GCS notification across all active GCP buckets
    with open(access_model_path, "r", encoding="utf-8") as f:
        access_model = json.load(f)

    gcp_buckets = list(set(access_model.get("GCP_RBAC", {}).values()))
    if not gcp_buckets:
        print("[GCP][WARN] No GCP buckets found in generated_access_model.json.")
        return topic_name, sub_name

    print(f"\n[4] Attaching GCS event notifications to {len(gcp_buckets)} buckets...")
    short_topic = "permission-rag-gcp-drift-topic"
    attached_count = 0

    for b_name in gcp_buckets:
        try:
            bucket = gcp_storage.bucket(b_name)
            notification = bucket.notification(
                topic_name=short_topic,
                topic_project=project_id,
                payload_format="JSON_API_V1",
                event_types=["OBJECT_FINALIZE"]
            )
            notification.create()
            print(f"    - [Attached] {b_name} -> {short_topic}")
            attached_count += 1
        except Exception as e:
            if "already exists" in str(e).lower() or "conflict" in str(e).lower():
                print(f"    - [INFO] Notification already attached to {b_name}")
                attached_count += 1
            else:
                print(f"    - [WARN] Could not attach to {b_name}: {e}")

    print("\n" + "=" * 80)
    print("  GCP PUB/SUB DRIFT QUEUE SETUP COMPLETE!")
    print(f"  Buckets configured: {attached_count}/{len(gcp_buckets)}")
    print(f"  Topic:              {topic_name}")
    print(f"  Subscription:       {sub_name}")
    print("=" * 80 + "\n")

    return topic_name, sub_name


if __name__ == "__main__":
    setup_gcp_pubsub()
