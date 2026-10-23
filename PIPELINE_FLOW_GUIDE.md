# Permission-Aware RAG: End-to-End Pipeline & File Reference Guide

This document provides a comprehensive, step-by-step walkthrough of the entire project pipeline, detailing:
1. **Where to place input datasets.**
2. **Step-by-step execution flow (`01` through `09`).**
3. **What each script does, what inputs it takes, and what files it generates.**
4. **Complete file-by-file reference for all scripts, core modules, and artifacts.**

---

## 🚀 Part 1: Step-by-Step Pipeline Flow

```
[00. Dataset] ──▶ [01. Blueprint] ──▶ [02. Cloud Setup] ──▶ [03. Extract Texts]
                                                                     │
[07. FAISS Index] ◀── [06. Question List] ◀── [05. User Map] ◀── [04. Upload Cloud]
       │
       ▼
[08. Permission Evaluation] ──▶ [09. Metrics Table]
```

---

### Step 0: Place the Dataset File
Download or place the official **HotpotQA Distractor** split into the `artifacts/` folder:

* **File Location:** `artifacts/hotpot_dev_distractor_v1.json`
* **What it contains:** Multi-hop questions, reference gold answers, and ~13,783 Wikipedia context articles.
* **Download command:**
  ```bash
  curl -Lo artifacts/hotpot_dev_distractor_v1.json https://hotpotqa.s3.amazonaws.com/hotpot_dev_distractor_v1.json
  ```

---

### Step 1: Define the Security Blueprint & Access Model
```bash
python scripts/01_generate_access_model.py
```
* **What it does:**  
  Generates the access control design without touching the cloud yet.
  - Generates a **random 8-character prefix** (e.g., `cyvcyfpb`) to ensure bucket names are globally unique across all AWS & GCP accounts in the world.
  - Defines 5 GCP buckets (RBAC) and 5 AWS S3 buckets (ABAC).
  - Hardcodes the 8 standardized users (`user-1` to `user-7` and `baseline-admin`) and assigns their bucket permissions.
* **Input:** None (hardcoded configuration).
* **Output:**  
  - 📄 `artifacts/generated_access_model.json` *(The access control rulebook)*.

---

### Step 2: Create Cloud Resources & User Credentials
```bash
python scripts/02_gcp_aws_resource_creator.py
```
* **What it does:**  
  Connects live to GCP and AWS using your `.env` admin credentials to build the actual cloud infrastructure based on the Step 1 blueprint:
  - Creates the 5 GCP buckets and 5 AWS S3 buckets.
  - Creates 8 GCP user service accounts and attaches `roles/storage.objectViewer` bucket IAM policies.
  - Creates 8 AWS IAM users, tags them for ABAC conditions, and attaches S3 policies.
  - Generates individual private JSON keys for GCP users and AWS Access Keys for AWS users.
* **Inputs:**  
  - `.env` (Admin keys)
  - `artifacts/generated_access_model.json`
* **Outputs:**  
  - ☁️ **Live Cloud:** 10 live buckets + 16 user identities in GCP and AWS.  
  - 📄 `artifacts/test_credential.txt` *(Contains all AWS access keys and GCP private JSON keys per user)*.

---

### Step 3: Extract Articles into Text Files & Map to Buckets
```bash
python scripts/03_prepare_documents.py
```
* **What it does:**  
  Extracts each of the ~13,783 Wikipedia articles from `hotpot_dev_distractor_v1.json`:
  - Sanitizes article titles into clean filenames (e.g., `15_penn_plaza.txt`).
  - Distributes the files across the 5 GCP buckets and 5 AWS S3 buckets.
  - Prepares local `.txt` files on your disk before uploading.
* **Inputs:**  
  - `artifacts/hotpot_dev_distractor_v1.json`
  - `artifacts/generated_access_model.json`
* **Outputs:**  
  - 📁 `artifacts/resources/` *(10 subfolders containing the raw `.txt` files)*.  
  - 📄 `artifacts/file_to_storage_info.json` *(The master mapping: links each file to its cloud provider, bucket, and attributes)*.

---

### Step 4: Upload Documents to Cloud Storage
```bash
python scripts/04_upload_resources.py
```
* **What it does:**  
  Reads `file_to_storage_info.json` and uses multi-threading to upload every `.txt` file from `artifacts/resources/` into the live GCP and AWS buckets.
* **Inputs:**  
  - `artifacts/file_to_storage_info.json`
  - `artifacts/resources/` folder
* **Outputs:**  
  - ☁️ **Live Cloud:** All 13,783 document files are now stored inside your live cloud buckets.

---

### Step 5: Generate Ground-Truth User Visibility Map
```bash
python scripts/05_generate_user_accessible_file_list.py
```
* **What it does:**  
  Calculates the mathematical ground-truth visibility table: for every user (`user-1` to `user-7` and `baseline-admin`), it compiles the exact list of files that user is authorized to read.
* **Inputs:**  
  - `artifacts/generated_access_model.json`
  - `artifacts/file_to_storage_info.json`
* **Outputs:**  
  - 📄 `artifacts/user_accessible_files.json` *(Used during evaluation to verify True Positives and ensure zero unauthorized access leaks)*.

---

### Step 6: Extract Evaluation Questions List
```bash
python scripts/06_generate_question_list.py
```
* **What it does:**  
  Pulls out clean question IDs, question strings, and gold reference answers from `hotpot_dev_distractor_v1.json`.
* **Input:**  
  - `artifacts/hotpot_dev_distractor_v1.json`
* **Output:**  
  - 📄 `artifacts/question_list.json` *(Input question pool for the evaluation)*.

---

### Step 7: Build the Centralized FAISS Vector Store
```bash
python scripts/07_run_ingestion_manager.py
```
* **What it does:**  
  Reads the document records, computes dense vector embeddings using `SentenceTransformer` (`all-MiniLM-L6-v2` or `BAAI/bge-base-en-v1.5`), and builds the FAISS similarity index.
* **Inputs:**  
  - `artifacts/hotpot_dev_distractor_v1.json`
  - `artifacts/file_to_storage_info.json`
* **Outputs:**  
  - 📦 `artifacts/faiss.index` *(Binary FAISS vector index)*.  
  - 📄 `artifacts/faiss_metadata.json` *(Vector index ID ➔ Document UUID mapping)*.  
  - 📄 `artifacts/metadata.json` *(Document UUID ➔ Storage provider, bucket, and filename mapping)*.

---

### Step 8: Run the Quantitative Permission-Aware Evaluation
```bash
python scripts/08_run_quantitative_evaluation.py
```
* **What it does:**  
  The core experimental benchmark:
  1. Samples questions from `question_list.json`.
  2. For each query, retrieves Top-k candidate chunks from `faiss.index`.
  3. **Live IAM Check:** Authenticates as the target user using `test_credential.txt` and queries GCP/AWS in real time. Chunks returning `403 Forbidden` are immediately blocked.
  4. Feeds only authorized chunks into Gemini LLM with strict grounding prompt instructions.
  5. Measures Permission Coverage (`PermCov`), Exact Match (`EM`), F1 Score, and `EM@Admin`.
* **Inputs:**  
  - `artifacts/question_list.json`
  - `artifacts/metadata.json`
  - `artifacts/faiss.index`
  - `artifacts/test_credential.txt`
  - `artifacts/user_accessible_files.json`
* **Outputs:**  
  - 📄 `results/quantitative_summary.json` *(Summary metrics per user)*.  
  - 📄 `results/quantitative_details.json` *(Detailed per-question retrieved vs. accessed documents)*.  
  - 📁 `results/preds/preds_<user>.json` *(Official HotpotQA prediction files)*.  
  - 📄 `results/test_execution.log` *(Detailed execution log)*.

---

### Step 9: Print the Formatted Metrics Table
```bash
python scripts/generate_metrics_table.py
```
* **What it does:**  
  Parses `results/quantitative_summary.json` and `results/quantitative_details.json` and prints the final research paper metric table directly to your terminal.
* **Inputs:**  
  - `results/quantitative_summary.json`
  - `results/quantitative_details.json`
* **Output:**  
  - 📄 `results/metrics_table.txt` *(ASCII research summary table)*.

---

## 📁 Part 2: Complete File-by-File Reference

### Core Architecture Modules (`src/`)

| File | Purpose | Outputs / Artifacts |
| :--- | :--- | :--- |
| `src/stores/vector_store.py` | FAISS index builder and similarity search wrapper. | `artifacts/faiss.index`, `artifacts/faiss_metadata.json` |
| `src/stores/metadata_store.py` | In-memory lookup registry for document storage metadata. | None (in-memory) |
| `src/retrievers/permission_retriever.py` | Concurrently checks user access with IAM adapters and strips 403 denied chunks. | In-memory filtered chunks & decision timings |
| `src/processors/model_fallback.py` | Automatic fallback client across models in `models.ini`. | LLM inference client |
| `src/processors/question_processor.py` | Runs RAG query loop, permission filtering, grounded prompt, and LLM answer generation. | Evaluation metrics & prediction records |
| `src/managers/credential_manager.py` | Loads `test_credential.txt` and instantiates user adapter factories. | In-memory user credentials |
| `src/managers/ingestion_manager.py` | Orchestrates embedding generation and FAISS index persistence. | `artifacts/faiss.index`, `metadata.json` |
| `src/adapters/iam/base.py` | Abstract base interface `AccessControlBase`. | None |
| `src/adapters/iam/aws.py` | Live S3 access check via `s3.head_object`. | Live S3 200/403 decisions |
| `src/adapters/iam/gcp.py` | Live GCS access check via `blob.test_iam_permissions`. | Live GCS 200/403 decisions |
| `src/adapters/iam/keycloak.py` | On-premise Keycloak OAuth2/OIDC adapter. | None |
| `src/adapters/iam/adapter_factory.py` | Routes provider names to adapter instances. | In-memory adapter instances |

---

### Custom Pipeline Scripts (`scripts/custom_0*`)

| File | Purpose | Outputs / Artifacts |
| :--- | :--- | :--- |
| `scripts/custom_01_generate_access_model.py` | Generates enterprise corporate roles (Auditor, Engineer, HR, Finance). | `artifacts/custom_artifacts/generated_access_model.json`, `test_credential.txt`, `user_accessible_files.json` |
| `scripts/custom_02_ingest_vector_store.py` | Hybrid ingestion using dense FAISS (`BAAI/bge-base-en-v1.5`) + BM25 sparse index. | `artifacts/custom_artifacts/faiss_dense.index`, `bm25.pkl`, `metadata.json` |
| `scripts/custom_03_run_quantitative_evaluation.py` | Quantitative evaluation across corporate roles with local GCP caching and live AWS checks. | `results/custom_results/quantitative_summary.json`, `custom_metrics.txt` |

---

### Diagnostics, Maintenance & Teardown Scripts (`scripts/`)

| File | Purpose | Outputs / Artifacts |
| :--- | :--- | :--- |
| `scripts/test_live_gcp_user_permissions.py` | Issues live JSON keys for all 8 users and runs 40 live permission checks against GCP buckets. | Updates `artifacts/test_credential.txt` with real keys |
| `scripts/test_gcp_mock_pipeline.py` | Creates and tests temporary mock buckets (`--create`) and safely tears them down (`--delete`). | `artifacts/mock_test_buckets.json` |
| `scripts/test_multihop_analysis.py` | Analyzes multi-hop reasoning chains on sample HotpotQA questions. | Terminal output |
| `scripts/test_hotpotqa_evaluation.py` | Unit test for HotpotQA EM and F1 evaluation metrics. | Terminal output |
| `scripts/provision_aws.py` | Standalone AWS provisioner (buckets, IAM users, ABAC policies). | AWS resources |
| `scripts/deprovision_aws.py` | Standalone AWS cleaner (deletes S3 buckets, keys, and users). | Cleans AWS resources |
| `scripts/provision_gcp.py` | Standalone GCP provisioner (buckets and service accounts). | GCP resources |
| `scripts/deprovision_gcp.py` | Standalone GCP cleaner (deletes GCS buckets and service accounts). | Cleans GCP resources |
| `scripts/cleanup_resources.py` | Unified teardown for both AWS and GCP in one command. | Cleans both cloud providers |

---

### Evaluation Metric Helpers (`artifacts/`)

| File | Purpose |
| :--- | :--- |
| `artifacts/hotpot_evaluate_v1.py` | Standard HotpotQA evaluation script (EM and F1 metrics). |
| `artifacts/hotpot_evaluate_with_em_admin.py` | Extended evaluation script computing standard EM/F1 plus `EM@Admin`. |

---

## 📊 Summary of All Generated Artifacts

| Generated File | Step / Script | What It Contains |
| :--- | :--- | :--- |
| `artifacts/generated_access_model.json` | Step 01 | RBAC/ABAC rules, user assignments, bucket names |
| `artifacts/test_credential.txt` | Step 02 | AWS access keys and GCP private JSON keys for all users |
| `artifacts/resources/` | Step 03 | ~13,783 extracted `.txt` document files across 10 folders |
| `artifacts/file_to_storage_info.json` | Step 03 | Master mapping of every file to its provider and bucket |
| `artifacts/user_accessible_files.json` | Step 05 | Ground-truth list of authorized filenames per user |
| `artifacts/question_list.json` | Step 06 | Clean list of HotpotQA questions and gold answers |
| `artifacts/faiss.index` | Step 07 | Binary FAISS vector index |
| `artifacts/faiss_metadata.json` | Step 07 | Vector index ID ➔ Document UUID map |
| `artifacts/metadata.json` | Step 07 | Document UUID ➔ Storage metadata map |
| `results/quantitative_summary.json` | Step 08 | Summary accuracy and permission coverage metrics |
| `results/quantitative_details.json` | Step 08 | Per-question logs of retrieved vs. authorized documents |
| `results/preds/preds_<user>.json` | Step 08 | HotpotQA formatted prediction answers per user |
| `results/metrics_table.txt` | Step 09 | Formatted research paper metrics table |

