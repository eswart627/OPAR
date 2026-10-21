"""
scripts/custom_03_run_quantitative_evaluation.py

Step 3: Quantitative Permission-Aware Evaluation on MultiHop Dataset.
Runs the complete multi-hop QA pipeline across corporate user roles:
  - user-1: Security Auditor (can only see security policies)
  - user-2: Engineer (can see engineering and security)
  - user-3: HR Specialist (can see HR and security)
  - user-4: Finance Analyst (can see payroll and financial reports)
  - baseline-admin: Full access to all 5 buckets

Features:
  - Single (concurrency=1) or Parallel (concurrency=N) execution modes.
  - Hybrid (BM25 + FAISS) or Dense FAISS vector retrieval (768 dimensions).
  - Uses local IAM caching (no GCP API errors / zero key dependencies).
  - Formats evaluation results into custom_metrics.txt and quantitative_summary.json.
"""

import os
import sys
import json
import time
import pickle
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.stores.metadata_store import MetadataStore
from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig
from src.managers.credential_manager import CredentialManager
from src.processors.question_processor import QuestionProcessor


# -----------------------------------------------------------------------------
# Hybrid / FAISS Search Providers
# -----------------------------------------------------------------------------

class HybridRetrieverProvider:
    def __init__(self, index_path: str, meta_path: str, bm25_path: str, model_name: str = "BAAI/bge-base-en-v1.5"):
        self.model = SentenceTransformer(model_name)
        self.faiss_index = faiss.read_index(index_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            self.vector_meta = json.load(f)
        with open(bm25_path, "rb") as f:
            self.bm25 = pickle.load(f)

    def search(self, query: str, top_k: int = 5, rrf_k: int = 60) -> List[Dict[str, Any]]:
        pool_size = max(top_k * 4, 30)

        # BM25 scores
        q_tokens = [w.lower() for w in query.split()]
        bm25_scores = self.bm25.get_scores(q_tokens)
        bm25_top_idx = np.argsort(bm25_scores)[::-1][:pool_size]

        # Dense FAISS scores
        q_emb = self.model.encode([query], convert_to_numpy=True).astype(np.float32)
        _, dense_top_idx = self.faiss_index.search(q_emb, pool_size)

        # Reciprocal Rank Fusion (RRF)
        rrf_scores: Dict[int, float] = defaultdict(float)
        for rank, idx in enumerate(bm25_top_idx, start=1):
            rrf_scores[idx] += 1.0 / (rrf_k + rank)
        for rank, idx in enumerate(dense_top_idx[0], start=1):
            if 0 <= idx < len(self.vector_meta):
                rrf_scores[idx] += 1.0 / (rrf_k + rank)

        ranked = sorted(rrf_scores.keys(), key=lambda i: rrf_scores[i], reverse=True)[:top_k]
        results = []
        for idx in ranked:
            row = self.vector_meta[idx].copy()
            row["score"] = rrf_scores[idx]
            results.append(row)
        return results


class DenseFAISSRetrieverProvider:
    def __init__(self, index_path: str, meta_path: str, model_name: str = "BAAI/bge-base-en-v1.5"):
        self.model = SentenceTransformer(model_name)
        self.faiss_index = faiss.read_index(index_path)
        with open(meta_path, "r", encoding="utf-8") as f:
            self.vector_meta = json.load(f)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        q_emb = self.model.encode([query], convert_to_numpy=True).astype(np.float32)
        distances, indices = self.faiss_index.search(q_emb, top_k)
        results = []
        for idx, dist in zip(indices[0], distances[0]):
            if 0 <= idx < len(self.vector_meta):
                row = self.vector_meta[idx].copy()
                row["distance"] = float(dist)
                results.append(row)
        return results


# -----------------------------------------------------------------------------
# Main Evaluation Runner
# -----------------------------------------------------------------------------

def run_evaluation(
    dataset_dir: str,
    store_type: str = "hybrid",
    concurrency: int = 1,
    top_k: int = 5,
    target_users: Optional[List[str]] = None
):
    print("=" * 85)

    custom_artifacts = PROJECT_ROOT / "artifacts" / "custom_artifacts"
    custom_results = PROJECT_ROOT / "results" / "custom_results"
    custom_results.mkdir(parents=True, exist_ok=True)

    dataset_path = Path(dataset_dir)
    question_file = dataset_path / "question_list.json"
    meta_file = custom_artifacts / "metadata.json"
    cred_file = custom_artifacts / "test_credential.txt"
    index_file = custom_artifacts / f"faiss_{store_type}.index"
    vecmeta_file = custom_artifacts / f"faiss_{store_type}_metadata.json"
    bm25_file = custom_artifacts / "bm25.pkl"

    if not index_file.exists():
        raise FileNotFoundError(f"Vector index not found at: {index_file}. Please run Step 02 first.")

    # 1. Load Stores and Managers
    print("\n[1/3] Initializing Vector Store and Credential Manager...")
    if store_type == "hybrid":
        store = HybridRetrieverProvider(str(index_file), str(vecmeta_file), str(bm25_file))
    else:
        store = DenseFAISSRetrieverProvider(str(index_file), str(vecmeta_file))

    user_manager = CredentialManager(base_path=str(custom_artifacts), file_name="test_credential.txt")
    ms = MetadataStore()
    ms.load(str(meta_file))

    pr = PermissionRetriever(ms, PermissionRetrieverConfig(max_workers=10, use_cache=True))

    qp = QuestionProcessor(
        question_file=str(question_file),
        metadata_file=str(meta_file),
        vector_store=store,
        user_manager=user_manager,
        permission_retriever=pr,
    )

    # 2. Run Evaluation Across Roles
    active_users = target_users or ["user-1", "user-2", "user-3", "user-4", "baseline-admin"]
    print(f"\n[2/3] Evaluating {len(qp.questions)} multi-hop questions across {len(active_users)} roles: {active_users}\n")

    summary_rows = []
    details_all = []

    # Local State Access Controller: GCP uses local state cache; AWS uses AWSAccessControl
    vis_file = custom_artifacts / "user_accessible_files.json"
    with open(vis_file, "r", encoding="utf-8") as vf:
        user_visibility_map = json.load(vf)

    from src.adapters.iam.aws import AWSAccessControl

    class LocalGCPAccessControl:
        def __init__(self, username: str, user_files: set, is_admin: bool):
            self.username = username
            self.user_files = user_files
            self.is_admin = is_admin

        def check_access(self, resource_meta: Dict[str, Any]) -> bool:
            if self.is_admin:
                return True
            fname = resource_meta.get("file_name", "")
            return fname in self.user_files

    class HybridCloudAdapterManager:
        def __init__(self, username: str, user_files: set, is_admin: bool, user_creds: dict):
            self.user_name = username
            self.gcp_ctrl = LocalGCPAccessControl(username, user_files, is_admin)
            self.aws_ctrl = AWSAccessControl(user_creds)

        def get_access_controller(self, provider: str = ""):
            p = (provider or "").lower().strip()
            if p == "aws":
                return self.aws_ctrl
            # GCP and other cloud files check local state cache (no GCP user json keys needed)
            return self.gcp_ctrl


    for user_name in active_users:
        if user_name not in user_manager.users:
            continue
        print(f"--- Running evaluation for role: [{user_name}] ---")
        
        # Build hybrid cloud adapter: GCP -> local state cache, AWS -> AWSAccessControl with real secret keys
        u_info = user_visibility_map.get(user_name, {})
        is_admin = bool(u_info.get("full_access"))
        u_files = set(u_info.get("files", []))
        user_creds = user_manager.get_user_credentials(user_name)
        ac_manager = HybridCloudAdapterManager(user_name, u_files, is_admin, user_creds)

        t0 = time.time()
        result = qp.run_for_quantitative(
            ac_manager=ac_manager,
            user_name=user_name,
            top_k=top_k,
            max_workers=10,
            question_concurrency=concurrency,
            use_permission_cache=True,
        )
        elapsed = time.time() - t0
        agg = result.get("aggregate", {})

        print(f"  Result [{user_name}]: PermCov: {agg.get('PermissionCoverage_pct', 0):.2f}% | "
              f"AccessMatch: {'PASS' if agg.get('AccessMatch') else 'FAIL'} | "
              f"Docs Authorized: {agg.get('total_authorized', 0)}/{agg.get('total_retrieved', 0)} | "
              f"Time: {elapsed:.2f}s")

        row = {
            "user": user_name,
            "role": user_manager.users[user_name].get("role", "N/A"),
            "top_k": top_k,
            "concurrency": concurrency,
            "store_type": store_type,
            "PermCov_pct": round(agg.get("PermissionCoverage_pct", 0.0), 2),
            "AccessMatch": "PASS" if agg.get("AccessMatch") else "FAIL",
            "total_retrieved": agg.get("total_retrieved", 0),
            "total_authorized": agg.get("total_authorized", 0),
            "elapsed_sec": round(elapsed, 2),
        }
        summary_rows.append(row)
        details_all.append({"user": user_name, "result": result})

    # 3. Save Summary & Formatted Metrics
    print("\n[3/3] Saving summary reports...")
    summary_path = custom_results / "quantitative_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_rows, f, indent=2, ensure_ascii=False)

    # Pretty metrics table
    table_lines = [
        "=" * 105,
        "             CORPORATE MULTI-HOP DATASET: PERMISSION-AWARE RAG EVALUATION",
        "=" * 105,
        f"Retrieval Store: {store_type.upper()} (768 dims) | Concurrency: {concurrency} | Top-K: {top_k}",
        "-" * 105,
        f"{'User ID':<16} | {'Role':<26} | {'PermCov (%)':<12} | {'Match':<7} | {'Docs Auth / Retr':<16}",
        "-" * 105,
    ]
    for r in summary_rows:
        table_lines.append(
            f"{r['user']:<16} | {r['role']:<26} | {str(r['PermCov_pct']) + '%':<12} | {r['AccessMatch']:<7} | {str(r['total_authorized']) + ' / ' + str(r['total_retrieved']):<16}"
        )
    table_lines.append("=" * 105)

    # Load question metadata (supporting facts, acceptable answers, etc.)
    q_map = {}
    if question_file.exists():
        try:
            with open(question_file, "r", encoding="utf-8") as qf:
                raw_qdata = json.load(qf)
                if isinstance(raw_qdata, list):
                    for item in raw_qdata:
                        qid = item.get("id") or item.get("question")
                        q_map[qid] = item
                        q_map[item.get("question", "")] = item
        except Exception as e:
            print(f"Warning: Could not parse question metadata: {e}")

    # Generate detailed evaluation report lines
    detail_lines = [
        "",
        "#" * 105,
        "                         DETAILED QUESTION-BY-QUESTION EVALUATION REPORT",
        "#" * 105,
    ]

    for item in details_all:
        user_name = item["user"]
        u_info = user_visibility_map.get(user_name, {})
        u_role = user_manager.users.get(user_name, {}).get("role", "N/A")
        u_creds = user_manager.users.get(user_name, {})
        gcp_cred = u_creds.get("gcp_credential", {})
        allowed_buckets = gcp_cred.get("allowed_buckets", [])
        is_admin = bool(u_info.get("full_access") or gcp_cred.get("full_access", False))
        u_files = u_info.get("files", [])

        detail_lines.append("")
        detail_lines.append("=" * 105)
        detail_lines.append(f"USER: {user_name}  |  ROLE: {u_role}")
        detail_lines.append(f"PERMISSIONS / ALLOWED BUCKETS: {allowed_buckets if not is_admin else ['* (FULL CLOUD ROOT ACCESS)']}")
        detail_lines.append(f"FULL ACCESS: {is_admin}  |  TOTAL ACCESSIBLE FILES IN IAM: {len(u_files)}")
        detail_lines.append("=" * 105)

        u_result = item["result"]
        by_question = u_result.get("by_question", [])

        for idx, q_res in enumerate(by_question, start=1):
            qid = q_res.get("question_id") or ""
            qtext = q_res.get("question", "")
            q_meta = q_map.get(qid) or q_map.get(qtext) or {}

            gold_answer = q_meta.get("gold_answer") or q_res.get("gold_answer", "")
            acceptable_answers = q_meta.get("acceptable_answers") or ([gold_answer] if gold_answer else [])
            supporting_facts = q_meta.get("supporting_facts", [])

            retrieved_docs = q_res.get("retrieved_docs", [])
            accessed_docs = q_res.get("accessed_docs", [])
            denied_docs = q_res.get("denied_docs", [])
            predicted_answer = (q_res.get("predicted_answer") or "").strip()

            # Check supporting docs catch status (Hit vs Miss)
            caught_supporting = [doc for doc in supporting_facts if doc in retrieved_docs]
            missed_supporting = [doc for doc in supporting_facts if doc not in caught_supporting]
            num_caught = len(caught_supporting)
            num_needed = len(supporting_facts)

            if num_needed == 0:
                retrieval_status = "N/A (No supporting facts specified)"
            elif num_caught == num_needed:
                retrieval_status = f"HIT (All {num_needed}/{num_needed} supporting docs caught: {caught_supporting})"
            elif num_caught == 0:
                retrieval_status = f"MISS (0/{num_needed} caught | Missing all: {missed_supporting})"
            else:
                retrieval_status = f"MISS (Only {num_caught}/{num_needed} caught | Caught: {caught_supporting} | Missing: {missed_supporting})"

            # Supporting docs permission status
            supporting_denied = [doc for doc in supporting_facts if doc in denied_docs]

            # Determine Verdict
            pred_lower = predicted_answer.lower()
            is_unknown = pred_lower in ["i don't know.", "i don't know", "i do not know.", "i do not know", "error", ""]
            
            is_correct = False
            for ans in acceptable_answers:
                ans_clean = ans.strip().lower()
                if ans_clean and (ans_clean in pred_lower or pred_lower in ans_clean):
                    is_correct = True
                    break

            if is_correct:
                verdict = "MATCH (CORRECT ANSWER OBTAINED)"
            elif is_unknown:
                if supporting_denied or not accessed_docs:
                    verdict = f"BLOCKED (ACCESS DENIED - SECURITY PRESERVED | Denied Supporting Hops: {supporting_denied})"
                else:
                    verdict = "BLOCKED / INSUFFICIENT CONTEXT"
            else:
                if supporting_denied:
                    verdict = f"MISMATCH / HALLUCINATION (UNAUTHORIZED CONTEXT | Denied Supporting Hops: {supporting_denied})"
                else:
                    verdict = "MISMATCH (INCORRECT ANSWER)"

            detail_lines.append(f"\n--- [Question {idx}] (ID: {qid}) ---")
            detail_lines.append(f"  QUESTION:               {qtext}")
            detail_lines.append(f"  GOLD ANSWER:            {gold_answer}")
            detail_lines.append(f"  ACCEPTABLE ANSWERS:     {acceptable_answers}")
            detail_lines.append(f"  SUPPORTING DOCS NEEDED: {supporting_facts}")
            detail_lines.append(f"  SUPPORTING RETRIEVAL:   {retrieval_status}")
            detail_lines.append(f"  RETRIEVED DOCS (Top {len(retrieved_docs)}):")
            for r_doc in retrieved_docs:
                auth_status = "ALLOWED" if r_doc in accessed_docs else "BLOCKED by IAM"
                is_supp = " [SUPPORTING HOP]" if r_doc in supporting_facts else ""
                detail_lines.append(f"    * {r_doc:<18} -> [{auth_status}]{is_supp}")
            detail_lines.append(f"  ALLOWED DOCS ({len(accessed_docs)}):        {accessed_docs}")
            detail_lines.append(f"  DENIED DOCS ({len(denied_docs)}):         {denied_docs}")
            detail_lines.append(f"  LLM ANSWER:             {predicted_answer}")
            detail_lines.append(f"  VERDICT:                {verdict}")

    full_report_text = "\n".join(table_lines + detail_lines)
    metrics_path = custom_results / "custom_metrics.txt"
    report_path = custom_results / "custom_evaluation_report.txt"

    with open(metrics_path, "w", encoding="utf-8") as f:
        f.write(full_report_text)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(full_report_text)

    print("\n" + "\n".join(table_lines))
    print(f"\n[SUCCESS] Reports saved to:")
    print(f"  - Clean Text Report: {report_path}")
    print(f"  - Metrics File:      {metrics_path}")
    print(f"  - JSON Summary:      {summary_path}")
    print("\n" + "=" * 85)
    print("STEP 03 COMPLETE.")
    print("=" * 85)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step 03: Run Quantitative Evaluation.")
    parser.add_argument(
        "--dataset-dir",
        type=str,
        default=r"C:\Users\pichi\Desktop\MultiHop_Dataset_Creation_Pipeline\data\permission_aware_export",
        help="Path to exported MultiHop dataset directory."
    )
    parser.add_argument(
        "--store-type",
        choices=["hybrid", "faiss"],
        default="hybrid",
        help="Retrieval store: 'hybrid' (BM25 + FAISS) or 'faiss' (dense only)."
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Execution concurrency (1 for sequential, >1 for parallel workers)."
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of retrieved documents per question."
    )
    parser.add_argument(
        "--users",
        nargs="+",
        default=None,
        help="Target roles to test (e.g. --users user-1 user-2 baseline-admin)."
    )
    args = parser.parse_args()

    run_evaluation(
        dataset_dir=args.dataset_dir,
        store_type=args.store_type,
        concurrency=args.concurrency,
        top_k=args.top_k,
        target_users=args.users
    )

