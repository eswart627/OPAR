"""
scripts/test_hotpotqa_evaluation.py

Runs 10 HotpotQA questions through Permission-Aware RAG with the exact same
detailed reporting format (user permissions, supporting docs HIT/MISS, retrieved docs,
IAM allowed/denied, LLM answer, and security verdict).
Does NOT modify any existing HotpotQA or custom pipeline code.
"""

import os
import sys
import re
import json
import time
import random
from pathlib import Path
from typing import Dict, Any, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.stores.vector_store import VectorStore
from src.stores.metadata_store import MetadataStore
from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig
from src.managers.credential_manager import CredentialManager
from src.processors.question_processor import QuestionProcessor
from src.adapters.iam.aws import AWSAccessControl


def sanitize_filename(title: str) -> str:
    title = title.lower().replace(" ", "_")
    return re.sub(r"[^\w\d_]", "", title)


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
        return self.gcp_ctrl


def run_hotpotqa_test(sample_size: int = 10, target_users: List[str] = None):
    if target_users is None:
        target_users = ["user-1", "user-3", "baseline-admin"]

    artifacts_dir = PROJECT_ROOT / "artifacts"
    results_dir = PROJECT_ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    question_file = artifacts_dir / "question_list.json"
    metadata_file = artifacts_dir / "metadata.json"
    faiss_index = artifacts_dir / "faiss.index"
    faiss_meta = artifacts_dir / "faiss_metadata.json"
    cred_file = artifacts_dir / "test_credential.txt"
    vis_file = artifacts_dir / "user_accessible_files.json"
    hotpot_dev_file = artifacts_dir / "hotpot_dev_distractor_v1.json"

    print("=" * 105)
    print("STARTING HOTPOTQA 10-QUESTION TEST RUN (DETAILED REPORTING)")
    print("=" * 105)

    # 1. Load HotpotQA Gold Data for Supporting Facts
    print("\n[1/4] Loading HotpotQA gold dataset for supporting facts mapping...")
    with open(hotpot_dev_file, "r", encoding="utf-8") as f:
        hotpot_dev = json.load(f)
    gold_map = {item["_id"]: item for item in hotpot_dev}

    # 2. Load Vector Store and Credentials
    print("\n[2/4] Loading FAISS index and stores...")
    vs = VectorStore(model_name="BAAI/bge-base-en-v1.5")
    vs.load(str(faiss_index), str(faiss_meta))

    ms = MetadataStore()
    ms.load(str(metadata_file))

    user_manager = CredentialManager(base_path=str(artifacts_dir), file_name="test_credential.txt")
    pr = PermissionRetriever(ms, PermissionRetrieverConfig(max_workers=10, use_cache=True))

    qp = QuestionProcessor(
        question_file=str(question_file),
        metadata_file=str(metadata_file),
        vector_store=vs,
        user_manager=user_manager,
        permission_retriever=pr,
    )
    qp.sample_questions(sample_size=sample_size, seed=42)
    print(f"Sampled {len(qp.sampled_questions)} questions with seed 42.")

    with open(vis_file, "r", encoding="utf-8") as vf:
        user_visibility_map = json.load(vf)

    # 3. Run Evaluation across roles
    print(f"\n[3/4] Running evaluation across roles: {target_users}...")
    summary_rows = []
    details_all = []

    for uname in target_users:
        if uname not in user_manager.users:
            continue
        print(f"\n--- Running evaluation for role: [{uname}] ---")
        u_info = user_visibility_map.get(uname, {})
        is_admin = bool(u_info.get("full_access"))
        u_files = set(u_info.get("files", []))
        user_creds = user_manager.get_user_credentials(uname)
        ac_manager = HybridCloudAdapterManager(uname, u_files, is_admin, user_creds)

        t0 = time.time()
        result = qp.run_for_quantitative(
            ac_manager=ac_manager,
            user_name=uname,
            top_k=5,
            max_workers=10,
            question_concurrency=1,
            use_permission_cache=True,
        )
        elapsed = time.time() - t0
        agg = result.get("aggregate", {})

        print(f"  Result [{uname}]: PermCov: {agg.get('PermissionCoverage_pct', 0):.2f}% | "
              f"Docs Authorized: {agg.get('total_authorized', 0)}/{agg.get('total_retrieved', 0)} | "
              f"Time: {elapsed:.2f}s")

        summary_rows.append({
            "user": uname,
            "PermCov_pct": round(agg.get("PermissionCoverage_pct", 0.0), 2),
            "AccessMatch": "PASS" if agg.get("AccessMatch") else "FAIL",
            "total_retrieved": agg.get("total_retrieved", 0),
            "total_authorized": agg.get("total_authorized", 0),
            "elapsed_sec": round(elapsed, 2),
        })
        details_all.append({"user": uname, "result": result})

    # 4. Generate Detailed Report
    print("\n[4/4] Generating detailed text report...")

    table_lines = [
        "=" * 105,
        "                     HOTPOTQA DATASET: PERMISSION-AWARE RAG EVALUATION",
        "=" * 105,
        f"Retrieval Store: DENSE FAISS (768 dims) | Concurrency: 1 | Top-K: 5 | Sample Size: {sample_size}",
        "-" * 105,
        f"{'User ID':<16} | {'PermCov (%)':<12} | {'Match':<7} | {'Docs Auth / Retr':<16} | {'Elapsed (s)':<12}",
        "-" * 105,
    ]
    for r in summary_rows:
        table_lines.append(
            f"{r['user']:<16} | {str(r['PermCov_pct']) + '%':<12} | {r['AccessMatch']:<7} | {str(r['total_authorized']) + ' / ' + str(r['total_retrieved']):<16} | {str(r['elapsed_sec']) + 's':<12}"
        )
    table_lines.append("=" * 105)

    detail_lines = [
        "",
        "#" * 105,
        "                         HOTPOTQA DETAILED QUESTION-BY-QUESTION REPORT",
        "#" * 105,
    ]

    for item in details_all:
        user_name = item["user"]
        u_info = user_visibility_map.get(user_name, {})
        u_creds = user_manager.users.get(user_name, {})
        gcp_cred = u_creds.get("gcp_credential", {})
        allowed_buckets = gcp_cred.get("allowed_buckets", [])
        is_admin = bool(u_info.get("full_access") or gcp_cred.get("full_access", False))
        u_files = u_info.get("files", [])

        detail_lines.append("")
        detail_lines.append("=" * 105)
        detail_lines.append(f"USER: {user_name}")
        detail_lines.append(f"PERMISSIONS / ALLOWED BUCKETS: {allowed_buckets if not is_admin else ['* (FULL CLOUD ROOT ACCESS)']}")
        detail_lines.append(f"FULL ACCESS: {is_admin}  |  TOTAL ACCESSIBLE FILES IN IAM: {len(u_files)}")
        detail_lines.append("=" * 105)

        by_question = item["result"].get("by_question", [])
        for idx, q_res in enumerate(by_question, start=1):
            qid = q_res.get("question_id", "")
            qtext = q_res.get("question", "")
            gold_item = gold_map.get(qid, {})

            gold_answer = gold_item.get("answer") or q_res.get("gold_answer", "")
            acceptable_answers = [gold_answer] if gold_answer else []

            # Map HotpotQA supporting facts [title, sentence_index] -> filename
            raw_sp = gold_item.get("supporting_facts", [])
            sp_files = []
            for sp in raw_sp:
                if isinstance(sp, (list, tuple)) and len(sp) > 0:
                    fn = f"{sanitize_filename(sp[0])}.txt"
                    if fn not in sp_files:
                        sp_files.append(fn)

            retrieved_docs = q_res.get("retrieved_docs", [])
            accessed_docs = q_res.get("accessed_docs", [])
            denied_docs = q_res.get("denied_docs", [])
            predicted_answer = (q_res.get("predicted_answer") or "").strip()

            caught_sp = [doc for doc in sp_files if doc in retrieved_docs]
            missed_sp = [doc for doc in sp_files if doc not in caught_sp]
            num_caught = len(caught_sp)
            num_needed = len(sp_files)

            if num_needed == 0:
                retrieval_status = "N/A"
            elif num_caught == num_needed:
                retrieval_status = f"HIT (All {num_needed}/{num_needed} supporting docs caught: {caught_sp})"
            elif num_caught == 0:
                retrieval_status = f"MISS (0/{num_needed} caught | Missing all: {missed_sp})"
            else:
                retrieval_status = f"MISS (Only {num_caught}/{num_needed} caught | Caught: {caught_sp} | Missing: {missed_sp})"

            supporting_denied = [doc for doc in sp_files if doc in denied_docs]

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
            detail_lines.append(f"  SUPPORTING DOCS NEEDED: {sp_files}")
            detail_lines.append(f"  SUPPORTING RETRIEVAL:   {retrieval_status}")
            detail_lines.append(f"  RETRIEVED DOCS (Top {len(retrieved_docs)}):")
            for r_doc in retrieved_docs:
                auth_status = "ALLOWED" if r_doc in accessed_docs else "BLOCKED by IAM"
                is_supp = " [SUPPORTING HOP]" if r_doc in sp_files else ""
                detail_lines.append(f"    * {r_doc:<26} -> [{auth_status}]{is_supp}")
            detail_lines.append(f"  ALLOWED DOCS ({len(accessed_docs)}):        {accessed_docs}")
            detail_lines.append(f"  DENIED DOCS ({len(denied_docs)}):         {denied_docs}")
            detail_lines.append(f"  LLM ANSWER:             {predicted_answer}")
            detail_lines.append(f"  VERDICT:                {verdict}")

    full_report = "\n".join(table_lines + detail_lines)
    report_file = results_dir / "hotpotqa_evaluation_report.txt"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(full_report)

    print("\n" + "\n".join(table_lines))
    print(f"\n[SUCCESS] HotpotQA report saved to: {report_file}")
    print("=" * 105)


if __name__ == "__main__":
    run_hotpotqa_test(sample_size=10, target_users=["user-1", "user-3", "baseline-admin"])

