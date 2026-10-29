# scripts/ops/deprovision_aws_drift_queue.py
# Purpose: Deprovisions / deletes the AWS SQS drift queue and detaches S3 bucket notifications.

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

env_path = os.path.join(project_root, "config", ".env")
load_dotenv(env_path, override=True)

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
AWS_ACCESS_KEY = os.environ.get("AWS_ADMIN_ACCESS_KEY")
AWS_SECRET_KEY = os.environ.get("AWS_ADMIN_SECRET_KEY")
QUEUE_NAME = "permission-rag-drift-queue"


def deprovision_aws_drift_queue():
    print("\n" + "=" * 70)
    print(">>> Deprovisioning AWS Drift Queue & S3 Notifications...")
    print("=" * 70)

    if not (AWS_ACCESS_KEY and AWS_SECRET_KEY):
        print("[AWS][WARN] No AWS admin credentials found; skipping AWS queue cleanup.")
        return

    import boto3
    from botocore.exceptions import ClientError

    sqs = boto3.client(
        "sqs",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )
    s3 = boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )

    # 1. Detach S3 notifications from active buckets
    access_model_path = os.path.join(project_root, "artifacts", "generated_access_model.json")
    if os.path.exists(access_model_path):
        import json
        with open(access_model_path, "r", encoding="utf-8") as f:
            model = json.load(f)
        aws_buckets = list(model.get("AWS_ABAC", {}).keys())
        for b_name in aws_buckets:
            try:
                s3.put_bucket_notification_configuration(
                    Bucket=b_name,
                    NotificationConfiguration={}
                )
                print(f"[AWS][INFO] Detached notification configuration from bucket: {b_name}")
            except Exception:
                pass  # Bucket may already be deleted

    # 2. Delete SQS Queue
    try:
        q_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
        sqs.delete_queue(QueueUrl=q_url)
        print(f"[AWS][SUCCESS] Deleted SQS Queue: {QUEUE_NAME} ({q_url})")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if "NonExistentQueue" in code or "QueueDoesNotExist" in code:
            print(f"[AWS][INFO] SQS Queue already deleted: {QUEUE_NAME}")
        else:
            print(f"[AWS][WARN] Could not delete SQS queue {QUEUE_NAME}: {e}")
    except Exception as e:
        print(f"[AWS][WARN] Error finding SQS queue {QUEUE_NAME}: {e}")

    print("=" * 70)
    print(">>> AWS Drift Queue Deprovisioning Complete.")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    deprovision_aws_drift_queue()
