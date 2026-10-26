# Operations & Multi-Cloud Lifecycle Guide (`Operation.md`)

This runbook documents the operational lifecycle, teardown orchestration, and maintenance workflows for the **Permission-Aware RAG** framework across **AWS**, **GCP**, and **Auth0**.

---

## 1. Quick Start: Teardown & Reset

When you want to tear down all cloud infrastructure, purge identities, and reset the local pipeline data to a clean state, use the automated sequential runner:

```powershell
# 1. Inspect all steps in the sequence without running them:
python scripts/ops/utils/run_teardown_sequence.py --list

# 2. View usage and help:
python scripts/ops/utils/run_teardown_sequence.py --help

# 3. Execute the full sequential teardown:
python scripts/ops/utils/run_teardown_sequence.py
```

---

## 2. The 8-Step Teardown Lifecycle

The teardown runner reads [`teardown_sequence.txt`](file:///c:/Users/pichi/Desktop/Permission%20Aware%20RAG/permission-aware-rag/teardown_sequence.txt) and executes each command strictly **one-by-one**. The sequence is ordered by strict cloud dependency rules:

| Step | Script Executed | Target Cloud / Domain | What It Deletes / Cleans Up |
| :---: | :--- | :--- | :--- |
| **1** | `scripts/ops/cloud/deprovision_aws.py` | **AWS (S3 & IAM)** | Empties and deletes 5 S3 buckets, 8 IAM users (`user-1` to `user-7`, `baseline-admin`), `abac-test-group`, and 5 ABAC policies. |
| **2** | `scripts/ops/cloud/deprovision_gcp.py` | **GCP (Storage & IAM)** | Empties and deletes 5 GCS buckets and 8 IAM Service Accounts. |
| **3** | `scripts/ops/identity/auth0_delete_user.py` | **Auth0 IdP** | Queries Auth0 Management API v2 and purges all `@company.internal` test users. |
| **4** | `scripts/ops/drift/deprovision_aws_drift_queue.py` | **AWS (SQS & Events)** | Detaches S3 bucket notifications and deletes the SQS queue (`permission-rag-drift-queue`). |
| **5** | `scripts/ops/drift/deprovision_gcp_drift_queue.py` | **GCP (Pub/Sub)** | Deletes Pub/Sub topic and subscription (`permission-rag-gcp-drift-*`). |
| **6** | `scripts/ops/identity/deprovision_aws_idp.py` | **AWS (Federation)** | Detaches policies, deletes IAM Role `Corporate-RAG-Auth0-Role`, deletes Auth0 OIDC provider, and removes `aws_idp_blueprint.json`. |
| **7** | `scripts/ops/utils/clean_generated_data.py` | **Local Storage** | Wipes local text resources, SQLite database (`metadata.db`), vector indices (`vector_db/`), previous evaluation results, and `generated_access_model.json`. |
| **8** | `scripts/ops/utils/audit_cloud_resources.py` | **Multi-Cloud Audit** | Real-time read-only inspection querying Auth0, AWS, and GCP live APIs to confirm 0 remaining resources. |

---

## 3. Operations Directory Architecture

All operational scripts are organized into domain-specific subdirectories under `scripts/ops/`:

```
scripts/ops/
├── cloud/                        # Cloud Storage & Infrastructure
│   ├── provision_aws.py          # Provisions 5 AWS S3 buckets, IAM users, groups, ABAC policies
│   ├── deprovision_aws.py        # Teardown for AWS storage & IAM
│   ├── provision_gcp.py          # Provisions 5 GCS buckets and 8 IAM service accounts
│   ├── deprovision_gcp.py        # Teardown for GCP storage & SAs
│   └── cleanup_resources.py      # Core shared cleanup logic for cloud resources
│
├── identity/                     # Corporate IdP & Token Federation
│   ├── auth0_create_user.py      # Provisions users with metadata in Auth0 tenant
│   ├── auth0_modify_user.py      # Modifies user attributes/roles to test dynamic drift
│   ├── auth0_delete_user.py      # Purges blueprint users from Auth0 tenant
│   ├── setup_aws_idp.py          # Provisions AWS OIDC provider & Corporate-RAG-Auth0-Role
│   ├── deprovision_aws_idp.py    # Tears down AWS OIDC provider & federation role
│   ├── setup_gcp_idp.py          # Provisions GCP Workload Identity Pool & Provider
│   └── deprovision_gcp_idp.py    # Soft-deletes GCP Workload Identity Federation
│
├── drift/                        # Event Queues & Real-Time Listeners
│   ├── setup_aws_drift_queue.py       # Provisions AWS SQS & S3 event notifications
│   ├── deprovision_aws_drift_queue.py # Deletes AWS SQS queue & detaches notifications
│   ├── setup_gcp_drift_queue.py       # Provisions GCP Pub/Sub topic & subscription
│   ├── deprovision_gcp_drift_queue.py # Deletes GCP Pub/Sub topic & subscription
│   ├── live_aws_drift_listener.py     # Live daemon listening to SQS events
│   ├── live_gcp_drift_listener.py     # Live daemon listening to Pub/Sub events
│   └── gcp_aws_drift_listener.py      # Dual-cloud unified listener daemon
│
└── utils/                        # Orchestration & Auditing Utilities
    ├── run_teardown_sequence.py  # Sequential orchestrator (--help, --list)
    ├── clean_generated_data.py   # Resets local artifacts & indices
    └── audit_cloud_resources.py  # Read-only health check across Auth0, AWS, and GCP
```

---

## 4. Important Safeguards & Cloud Rules

### 1. GCP 30-Day Workload Identity Soft-Delete
- When a GCP Workload Identity Pool (`auth0-pool`) is deleted, GCP retains it in a **30-day soft-deleted (tombstone) state**.
- During those 30 days, GCP **forbids creating a new pool with the exact same name**.
- **Best Practice:** Keep `auth0-pool` intact between test runs. Google Cloud charges **$0** for idle identity pools.

### 2. Protected Files (Never Deleted by Cleanup)
`clean_generated_data.py` enforces a strict safeguard list. The following files are **never** deleted:
- `artifacts/hotpot_dev_distractor_v1.json` (Base evaluation dataset)
- `config/.env` and `.env` (Project credentials)
- `config/Permission_Aware_Rag_GCP_ADMIN_KEY.json` (Admin service account key)
- `teardown_sequence.txt` (Teardown sequence definition)

### 3. Dependency Order Rules
- `auth0_delete_user.py` must run **before** `clean_generated_data.py`, because it reads `artifacts/generated_access_model.json` to identify which users to delete.
- Running `run_teardown_sequence.py` automatically enforces this order.
