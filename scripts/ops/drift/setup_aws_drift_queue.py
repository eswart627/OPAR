# scripts/ops/setup_aws_drift_queue.py
# Sets up AWS SQS queue and configures S3 event notifications for drift detection.
# Includes pre-flight validation and actionable error guidance for missing credentials or IAM permissions.

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Load environment
env_path = os.path.join(project_root, "config", ".env")
load_dotenv(env_path, override=True)

QUEUE_NAME = "permission-rag-drift-queue"


def validate_aws_prerequisites():
    """
    Validates all local and environment prerequisites before attempting cloud calls.
    Returns: (region, access_key, secret_key, access_model_path) or exits with code 1.
    """
    errors = []
    
    access_key = os.environ.get("AWS_ADMIN_ACCESS_KEY")
    secret_key = os.environ.get("AWS_ADMIN_SECRET_KEY")
    region = os.environ.get("AWS_REGION")
    
    if not access_key:
        errors.append("  - AWS_ADMIN_ACCESS_KEY is missing from config/.env")
    if not secret_key:
        errors.append("  - AWS_ADMIN_SECRET_KEY is missing from config/.env")
        
    if not region:
        errors.append(
            "  - AWS_REGION is missing from config/.env.\n"
            "    SQS queues and S3 event notifications strictly require an explicit AWS_REGION\n"
            "    (e.g. AWS_REGION=ap-south-1). Fallback defaults are disallowed."
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
        print("  [AWS PRE-FLIGHT ERROR] MISSING PREREQUISITES")
        print("=" * 80)
        for err in errors:
            print(err)
        print("\nHOW TO FIX:")
        print(f"  1. Open or create: {env_path}")
        print("  2. Ensure the following keys are defined:")
        print("       AWS_ADMIN_ACCESS_KEY=your_admin_access_key")
        print("       AWS_ADMIN_SECRET_KEY=your_admin_secret_key")
        print("       AWS_REGION=ap-south-1")
        print("=" * 80 + "\n")
        sys.exit(1)

    return region, access_key, secret_key, access_model_path


def setup_sqs_and_notifications():
    region, access_key, secret_key, access_model_path = validate_aws_prerequisites()

    print("=" * 80)
    print("  SETTING UP AWS SQS QUEUE & S3 EVENT NOTIFICATIONS FOR DRIFT DETECTION")
    print("=" * 80)

    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError, EndpointConnectionError
    except ImportError:
        print("\n[ERROR] boto3 is not installed. Please run:")
        print("  pip install boto3")
        sys.exit(1)

    # Initialize clients
    sqs = boto3.client(
        "sqs",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    s3 = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    sts = boto3.client(
        "sts",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    # Authenticate and resolve Account ID
    try:
        caller = sts.get_caller_identity()
        account_id = caller["Account"]
        caller_arn = caller["Arn"]
        print(f"[AWS][AUTH] Authenticated as: {caller_arn}")
        print(f"[AWS][INFO] Account ID: {account_id} | Region: {region}")
    except (NoCredentialsError, PartialCredentialsError) as e:
        print(f"\n[AWS][ERROR] Invalid or incomplete AWS credentials: {e}")
        print("Please verify AWS_ADMIN_ACCESS_KEY and AWS_ADMIN_SECRET_KEY in config/.env.")
        sys.exit(1)
    except EndpointConnectionError as e:
        print(f"\n[AWS][ERROR] Network connection error: {e}")
        print("Please check your internet connection or AWS network proxy.")
        sys.exit(1)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        print(f"\n[AWS][ERROR] STS Authentication failed ({error_code}): {e}")
        if error_code in ["InvalidClientTokenId", "SignatureDoesNotMatch"]:
            print("Action: Your AWS Access Key ID or Secret Key is incorrect. Please update config/.env.")
        sys.exit(1)

    # 1. Create or retrieve SQS Queue
    print(f"\n[1] Creating or retrieving SQS queue: '{QUEUE_NAME}' in region {region}...")
    try:
        q_resp = sqs.create_queue(
            QueueName=QUEUE_NAME,
            Attributes={
                "ReceiveMessageWaitTimeSeconds": "20",  # 20s long-polling
                "MessageRetentionPeriod": "86400",       # 1 day retention
            }
        )
        queue_url = q_resp["QueueUrl"]
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "AccessDenied":
            print("\n" + "=" * 80)
            print("  [AWS IAM ERROR] Access Denied when creating SQS Queue!")
            print("=" * 80)
            print("Your AWS IAM User lacks permissions to manage SQS queues.")
            print("\nHOW TO FIX:")
            print("  1. Open AWS IAM Users Console: https://console.aws.amazon.com/iam/home#/users")
            print(f"  2. Select your user ({caller_arn.split('/')[-1]})")
            print("  3. Click 'Add permissions' -> 'Attach policies directly'")
            print("  4. Search for and attach: 'AmazonSQSFullAccess'")
            print("  5. Re-run this script.")
            print("=" * 80 + "\n")
            sys.exit(1)
        elif "QueueAlreadyExists" in str(e) or error_code == "QueueNameExists":
            queue_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
        else:
            try:
                queue_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
            except Exception:
                print(f"[AWS][ERROR] Failed to create or get SQS queue: {e}")
                sys.exit(1)

    # Get Queue ARN
    try:
        attr_resp = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])
        queue_arn = attr_resp["Attributes"]["QueueArn"]
    except ClientError as e:
        print(f"[AWS][ERROR] Failed to get SQS Queue attributes: {e}")
        sys.exit(1)

    print(f"    -> [SUCCESS] Queue URL: {queue_url}")
    print(f"    -> [SUCCESS] Queue ARN: {queue_arn}")

    # 2. Attach SQS Access Policy so S3 can send notifications to this queue
    print(f"\n[2] Setting SQS Access Policy to allow S3 to send event notifications...")
    policy = {
        "Version": "2012-10-17",
        "Id": "AllowS3ToPublishEvents",
        "Statement": [
            {
                "Sid": "AllowS3Notifications",
                "Effect": "Allow",
                "Principal": {"Service": "s3.amazonaws.com"},
                "Action": "sqs:SendMessage",
                "Resource": queue_arn,
                "Condition": {
                    "ArnLike": {"aws:SourceArn": "arn:aws:s3:::*-aws-bucket-*"}
                }
            }
        ]
    }
    try:
        sqs.set_queue_attributes(
            QueueUrl=queue_url,
            Attributes={"Policy": json.dumps(policy)}
        )
        print("    -> [SUCCESS] Attached SQS policy allowing S3 to publish events.")
    except ClientError as e:
        print(f"    -> [WARN] Could not set queue policy: {e}")

    # 3. Read active AWS buckets from access model
    with open(access_model_path, "r", encoding="utf-8") as f:
        access_model = json.load(f)

    aws_buckets = list(access_model.get("AWS_ABAC", {}).keys())
    if not aws_buckets:
        print("[AWS][WARN] No AWS buckets found in generated_access_model.json.")
        return queue_url, queue_arn

    print(f"\n[3] Attaching S3 event notifications to {len(aws_buckets)} buckets...")

    notification_cfg = {
        "QueueConfigurations": [
            {
                "Id": "StorageDriftNotification",
                "QueueArn": queue_arn,
                "Events": ["s3:ObjectCreated:*"]
            }
        ]
    }

    attached_count = 0
    for bucket in aws_buckets:
        try:
            s3.put_bucket_notification_configuration(
                Bucket=bucket,
                NotificationConfiguration=notification_cfg
            )
            print(f"    - [Attached] {bucket} -> {QUEUE_NAME}")
            attached_count += 1
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "InvalidArgument":
                print(f"    - [ERROR] Bucket '{bucket}': Region mismatch!")
                print(f"              SQS queue is in region '{region}'. S3 notifications cannot cross regions.")
                print(f"              Please ensure the bucket and queue are in the exact same AWS region.")
            elif error_code == "AccessDenied":
                print(f"    - [ERROR] Bucket '{bucket}': Access Denied.")
                print(f"              Your user needs 's3:PutBucketNotificationConfiguration' on this bucket.")
            else:
                print(f"    - [ERROR] Failed to attach notification to {bucket}: {e}")

    print("\n" + "=" * 80)
    print("  AWS SQS QUEUE AND S3 BUCKET NOTIFICATIONS ACTIVE!")
    print(f"  Buckets configured: {attached_count}/{len(aws_buckets)}")
    print(f"  Queue URL:          {queue_url}")
    print(f"  Queue ARN:          {queue_arn}")
    print("=" * 80 + "\n")

    return queue_url, queue_arn


if __name__ == "__main__":
    setup_sqs_and_notifications()
