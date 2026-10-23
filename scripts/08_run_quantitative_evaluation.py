import os, sys, json, time, logging, argparse
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.stores.vector_store import VectorStore
from src.stores.bm25_store import BM25Store
from src.stores.metadata_store import MetadataStore
from src.retrievers.hybrid_retriever import HybridRetriever
from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig
from src.managers.credential_manager import CredentialManager
from src.processors.question_processor import QuestionProcessor

from scripts.helpers.hotpot_evaluate_with_em_admin import eval as hp_eval

load_dotenv()

# Setup root results directory and logger
ROOT_RESULTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results"))
LOGS_DIR = os.path.join(ROOT_RESULTS_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
log_file_path = os.path.join(LOGS_DIR, "test_execution.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("RAG_Eval")

def main():
    parser = argparse.ArgumentParser(description="Permission-Aware RAG Quantitative Evaluation")
    parser.add_argument(
        "--mode",
        choices=["faiss", "hybrid"],
        default="faiss",
        help="Retrieval mode: 'faiss' (dense vector only) or 'hybrid' (Weighted RRF with high BM25 weighting)",
    )
    args = parser.parse_args()
    retriever_mode = args.mode.lower()

    logger.info("=" * 80)
    logger.info(f"STARTING PERMISSION-AWARE RAG EVALUATION (MODE: {retriever_mode.upper()})")
    logger.info("=" * 80)

    ARTIFACTS = "./artifacts"
    VECTOR_DB = "./vector_db"
    QUESTION_FILE = os.path.join(ARTIFACTS, "question_list.json")
    METADATA_FILE = os.path.join(ARTIFACTS, "metadata.json")
    FAISS_INDEX = os.path.join(VECTOR_DB, "faiss.index")
    FAISS_META  = os.path.join(VECTOR_DB, "faiss_metadata.json")
    BM25_INDEX  = os.path.join(VECTOR_DB, "bm25.pkl")
    RESULTS_DIR = os.path.join(ROOT_RESULTS_DIR, retriever_mode)
    PRED_DIR = os.path.join(RESULTS_DIR, "preds")
    GOLD_FILE = os.path.join(ARTIFACTS, "hotpot_dev_distractor_v1.json")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(PRED_DIR, exist_ok=True)

    # ----- Experiment setting -----
    TOP_K = 5
    MAX_WORKERS = 10
    QUESTION_CONCURRENCY = 1
    USE_CACHE = True
    TARGET_USERS = {"user-1", "user-3", "baseline-admin"}
    SAMPLE_SIZE = 10
    
    # ----- Managers / Stores -----
    user_manager = CredentialManager(base_path=ARTIFACTS, file_name="test_credential.txt")
    vs = VectorStore(model_name="all-MiniLM-L6-v2")
    vs.load(FAISS_INDEX, FAISS_META)

    bm25 = None
    if retriever_mode == "hybrid":
        if not os.path.exists(BM25_INDEX):
            raise FileNotFoundError(f"BM25 index not found at {BM25_INDEX}. Run 07_run_ingestion_manager.py first.")
        bm25 = BM25Store()
        bm25.load(BM25_INDEX)

    # Instantiate HybridRetriever configured for the selected mode
    active_retriever = HybridRetriever(
        vector_store=vs,
        bm25_store=bm25,
        mode=retriever_mode,
        bm25_weight=0.75,
        faiss_weight=0.25,
        rrf_k=60,
    )

    ms = MetadataStore()
    ms.load(METADATA_FILE)

    # ----- Processor + PermissionRetriever -----
    pr = PermissionRetriever(ms, PermissionRetrieverConfig(max_workers=1, use_cache=USE_CACHE))
    qp = QuestionProcessor(QUESTION_FILE, METADATA_FILE, active_retriever, user_manager, permission_retriever=pr)
    qp.sample_questions(sample_size=SAMPLE_SIZE, seed=42)

    summary_rows = []
    details_all = []
    summary_index = {}
    
    t_perm_check_start = time.time()

    for user_name in user_manager.users.keys():
        if user_name not in TARGET_USERS:
            continue

        ac_manager = user_manager.get_ac_manager(user_name)

        t0 = time.time()

        result = qp.run_for_quantitative(
            ac_manager,
            user_name=user_name,
            top_k=TOP_K,
            max_workers=MAX_WORKERS,
            question_concurrency=QUESTION_CONCURRENCY,
        )

        elapsed = time.time() - t0
        print(f"[INFO] run_for_quantitative elapsed: {elapsed:.2f}s")

        aggregate = result.get("aggregate", {})
        by_question = result.get("by_question", [])

        preds = {
            "answer": {q["question_id"]: q["predicted_answer"] for q in by_question},
            "sp": {}
        }
        preds_file = os.path.join(PRED_DIR, f"preds_{user_name}.json")
        with open(preds_file, "w", encoding="utf-8") as f:
            json.dump(preds, f, indent=2, ensure_ascii=False)

        access_match = (aggregate.get("AccessMatch") == "PASS")
        perm_cov_pct = aggregate.get("PermissionCoverage_pct", 0.0)
        tp = aggregate.get("TP", 0)
        fp = aggregate.get("FP", 0)
        fn = aggregate.get("FN", 0)
        tn = aggregate.get("TN", 0)
        tot_auth = aggregate.get("total_authorized", 0)
        tot_retr = aggregate.get("total_retrieved", 0)
        match_str = aggregate.get("AccessMatch", "FAIL")

        print(
            f"- PermCov: {perm_cov_pct:.2f}% | "
            f"AccessMatch: {match_str} | "
            f"TP/FP/FN/TN: {tp}/{fp}/{fn}/{tn} | "
            f"Docs: {tot_auth}/{tot_retr} auth/retr\n"
        )

        row = {
            "user": user_name,
            "PermissionCoverage": perm_cov_pct / 100.0,
            "PermissionCoverage_pct": perm_cov_pct,
            "AccessMatch": access_match,
            "TP": tp,
            "FP": fp,
            "FN": fn,
            "TN": tn,
            "total_authorized": tot_auth,
            "total_retrieved": tot_retr,
            "em": None,
            "f1": None,
            "em_admin": None,
        }
        summary_rows.append(row)
        summary_index[user_name] = row

        details_all.append(
            {
                "user": user_name,
                "metrics": aggregate,
                "questions": by_question,
            }
        )

    t_perm_check_end = time.time()
    print(f"[TIMER] Permission checking and prediction generation took: {t_perm_check_end - t_perm_check_start:.2f}s")

    # ----- HotpotQA Evaluation (em, f1, em_admin) -----
    t_eval_start = time.time()
    ADMIN_USER = "baseline-admin"
    admin_pred_file = os.path.join(PRED_DIR, f"preds_{ADMIN_USER}.json")

    for user_name in user_manager.users.keys():
        if user_name not in TARGET_USERS:
            continue

        pred_file = os.path.join(PRED_DIR, f"preds_{user_name}.json")
        if not os.path.exists(pred_file):
            continue

        try:
            eval_res = hp_eval(pred_file, GOLD_FILE, admin_prediction_file=admin_pred_file)
            em = float(eval_res.get("em", 0.0))
            f1 = float(eval_res.get("f1", 0.0))
            em_admin = float(eval_res.get("em_admin", 0.0))

            if user_name in summary_index:
                summary_index[user_name]["em"] = em
                summary_index[user_name]["f1"] = f1
                summary_index[user_name]["em_admin"] = em_admin

            print(f"[INFO] evaluated {os.path.basename(pred_file)} -> em={em:.4f}, f1={f1:.4f}, em_admin={em_admin:.4f}")
        except Exception as e:
            print(f"[WARN] Failed to evaluate {pred_file}: {e}")

    t_eval_end = time.time()
    print(f"[TIMER] Final evaluation (EM, F1, EM@Admin) took: {t_eval_end - t_eval_start:.2f}s\n")

    # ----- Summary table to console -----
    print(
        f"[SUMMARY] "
        f"{[{'user': r['user'], 'PermCov': r['PermissionCoverage_pct'], 'Match': 'PASS' if r['AccessMatch'] else 'FAIL', 'EM': r['em'], 'F1': r['f1'], 'EM@Admin': r['em_admin']} for r in summary_rows]}"
    )

    # ----- Save outputs into mode-specific directory (results/<mode>/) -----
    summary_file = os.path.join(RESULTS_DIR, "quantitative_summary.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_rows, f, indent=2, ensure_ascii=False)

    details_file = os.path.join(RESULTS_DIR, "quantitative_details.json")
    with open(details_file, "w", encoding="utf-8") as f:
        json.dump(details_all, f, indent=2, ensure_ascii=False)

    # ----- Formatted Metrics Table (test_metrics.txt) -----
    total_time = round(t_eval_end - t_perm_check_start, 2)
    num_questions = len(qp.sampled_questions or qp.questions)
    retriever_label = "FAISS (all-MiniLM-L6-v2, 384-dim)" if retriever_mode == "faiss" else "Hybrid (0.75 BM25 + 0.25 FAISS 384-dim, RRF)"
    metric_table_lines = [
        "=" * 105,
        f"        PERMISSION-AWARE RAG: EXPERIMENT TEST METRICS SUMMARY (MODE: {retriever_mode.upper()})",
        "=" * 105,
        "Experiment Details:",
        f"  - Questions Tested:    {num_questions}",
        f"  - Top-k Retrieved:     {TOP_K}",
        f"  - Concurrency:         {QUESTION_CONCURRENCY} worker(s)",
        f"  - Target Users:        {', '.join(sorted(TARGET_USERS))}",
        f"  - Retriever Mode:      {retriever_label}",
        f"  - LLM Backend:         Gemini Fallback Engine (models.ini)",
        f"  - Total Elapsed Time:  {total_time}s",
        "-" * 105,
        f"{'User ID':<16} | {'PermCov (%)':<12} | {'Match':<7} | {'Exact Match':<12} | {'F1 Score':<10} | {'EM@Admin':<10} | {'Docs Auth / Retr':<16}",
        "-" * 105,
    ]

    for r in summary_rows:
        uname = r.get('user', '')
        pc = f"{r.get('PermissionCoverage_pct', 0.0):.2f}%"
        m = "PASS" if r.get('AccessMatch') else "FAIL"
        em_str = f"{r.get('em', 0.0):.4f}" if r.get('em') is not None else "N/A"
        f1_str = f"{r.get('f1', 0.0):.4f}" if r.get('f1') is not None else "N/A"
        ema_str = f"{r.get('em_admin', 0.0):.4f}" if r.get('em_admin') is not None else "N/A"
        docs_str = f"{r.get('total_authorized', 0)} / {r.get('total_retrieved', 0)}"

        metric_table_lines.append(
            f"{uname:<16} | {pc:<12} | {m:<7} | {em_str:<12} | {f1_str:<10} | {ema_str:<10} | {docs_str:<16}"
        )

    metric_table_lines.extend([
        "-" * 105,
        "Metric Definitions (IEEE Access Paper):",
        "  * PermCov (%):  Permission Coverage ratio of authorized documents (|D_auth| / |D_retr| * 100).",
        "  * Match:        PASS if no security leaks (FP=0) and no valid docs blocked (FN=0).",
        "  * EM:           Exact Match accuracy comparing LLM answer to gold dataset reference.",
        "  * F1:           Harmonic mean of token precision and recall against gold answer.",
        "  * EM@Admin:     Exact Match restricted to questions correctly answered by baseline-admin.",
        "=" * 105,
    ])

    metrics_text = "\n".join(metric_table_lines)
    metrics_path = os.path.join(RESULTS_DIR, "test_metrics.txt")
    with open(metrics_path, "w", encoding="utf-8") as f:
        f.write(metrics_text)
    
    logger.info("\n" + metrics_text)
    print(f"\n[INFO] Test metrics summary table saved to: {metrics_path}")

    # ----- Human-readable Text Report -----
    report_lines = [
        "=" * 80,
        f"PERMISSION-AWARE RAG: DETAILED EVALUATION REPORT (MODE: {retriever_mode.upper()})",
        "=" * 80,
        "",
    ]

    for user_block in details_all:
        user_name = user_block["user"]
        report_lines.append("=" * 80)
        report_lines.append(f"USER EVALUATION: {user_name}")
        report_lines.append("=" * 80)
        report_lines.append("")

        questions = user_block["questions"]
        for idx, q_info in enumerate(questions):
            qtext = q_info["question"]
            gold_ans = q_info.get("gold_answer", "")
            pred_ans = q_info.get("predicted_answer", "")
            retrieved_docs = q_info.get("retrieved_docs", [])
            accessed_docs = q_info.get("accessed_docs") or q_info.get("authorized_docs", [])
            denied_docs = q_info.get("denied_docs", [])
            supporting_docs = q_info.get("supporting_docs", [])

            if not accessed_docs:
                match_str = "BLOCKED (ACCESS DENIED - ANSWER PREVENTED)"
            elif pred_ans.strip().lower() == str(gold_ans).strip().lower():
                match_str = "MATCH (CORRECT)"
            elif pred_ans == "I don't know.":
                match_str = "BLOCKED (ACCESS DENIED - ANSWER PREVENTED)"
            else:
                match_str = "MISMATCH"

            report_lines.append(f"[Question {idx + 1}]")
            report_lines.append(f"  QUESTION:                {qtext}")
            report_lines.append(f"  SUPPORTING DOCS (GOLD):  {', '.join(supporting_docs) if supporting_docs else 'None'}")
            report_lines.append(f"  RETRIEVED DOCS ({len(retrieved_docs)}):")
            for rd in retrieved_docs:
                st = "ALLOWED" if rd in accessed_docs else "BLOCKED by IAM"
                sup_tag = " [SUPPORTING DOC]" if rd in supporting_docs else ""
                report_lines.append(f"    - {rd} [{st}]{sup_tag}")
            report_lines.append(f"  ACCESSED DOCS:           {', '.join(accessed_docs) if accessed_docs else 'None'}")
            report_lines.append(f"  DENIED DOCS:             {', '.join(denied_docs) if denied_docs else 'None'}")
            report_lines.append(f"  LLM PREDICTED ANSWER:    {pred_ans}")
            report_lines.append(f"  GOLDEN REFERENCE ANSWER: {gold_ans}")
            report_lines.append(f"  COMPARISON:              {match_str}")
            report_lines.append("-" * 80)
            report_lines.append("")

    report_text = "\n".join(report_lines)
    rep_path = os.path.join(RESULTS_DIR, "detailed_evaluation_report.txt")
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\n[INFO] Detailed evaluation report saved to: {rep_path}")
    print("[DONE] Saved qualitative summary, details, and text report.")

if __name__ == "__main__":
    main()
