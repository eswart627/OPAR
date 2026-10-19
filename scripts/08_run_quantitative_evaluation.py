import os, sys, json, time, logging
from pathlib import Path
from dotenv import load_dotenv

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.stores.vector_store import VectorStore
from src.stores.metadata_store import MetadataStore
from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig
from src.managers.credential_manager import CredentialManager
from src.processors.question_processor import QuestionProcessor

from artifacts.hotpot_evaluate_with_em_admin import eval as hp_eval

load_dotenv()

# Setup root results directory and logger
ROOT_RESULTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results"))
os.makedirs(ROOT_RESULTS_DIR, exist_ok=True)
log_file_path = os.path.join(ROOT_RESULTS_DIR, "test_execution.log")

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
    logger.info("=" * 80)
    logger.info("STARTING PERMISSION-AWARE RAG 10-QUESTION TEST RUN")
    logger.info("=" * 80)

    ARTIFACTS = "./artifacts"
    QUESTION_FILE = os.path.join(ARTIFACTS, "question_list.json")
    METADATA_FILE = os.path.join(ARTIFACTS, "metadata.json")
    FAISS_INDEX = os.path.join(ARTIFACTS, "faiss.index")
    FAISS_META  = os.path.join(ARTIFACTS, "faiss_metadata.json")
    RESULTS_DIR = ROOT_RESULTS_DIR
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
    vs = VectorStore(model_name="BAAI/bge-base-en-v1.5")
    vs.load(FAISS_INDEX, FAISS_META)

    ms = MetadataStore()
    ms.load(METADATA_FILE)

    # ----- Processor + PermissionRetriever -----
    pr = PermissionRetriever(ms, PermissionRetrieverConfig(max_workers=1, use_cache=USE_CACHE))
    qp = QuestionProcessor(QUESTION_FILE, METADATA_FILE, vs, user_manager, permission_retriever=pr)
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
            use_permission_cache=USE_CACHE,
        )
        t1 = time.time()
        print(f"[INFO] run_for_qualitative elapsed: {t1 - t0:.2f}s")

        agg = result["aggregate"]
        print(f"- PermCov: {agg['PermissionCoverage_pct']:.2f}% | "
              f"AccessMatch: {agg['AccessMatch']} | "
              f"TP/FP/FN/TN: {agg['TP']}/{agg['FP']}/{agg['FN']}/{agg['TN']} | "
              f"Docs: {agg['total_authorized']}/{agg['total_retrieved']} auth/retr")

        row = {
            "user": user_name,
            "top_k": TOP_K,
            "max_workers": MAX_WORKERS,
            "use_cache": USE_CACHE,
            "PermissionCoverage_pct": agg["PermissionCoverage_pct"],
            "AccessMatch": agg["AccessMatch"],
            "TP": agg["TP"], "FP": agg["FP"], "FN": agg["FN"], "TN": agg["TN"],
            "total_retrieved": agg["total_retrieved"],
            "total_authorized": agg["total_authorized"],
            "elapsed_sec": round(t1 - t0, 2),
            "em": None,
            "f1": None,
            "em_admin": None,
        }
        summary_rows.append(row)
        summary_index[user_name] = row

        details_all.append({
            "config": {"user": user_name, "top_k": TOP_K, "max_workers": MAX_WORKERS, "use_cache": USE_CACHE},
            "result": result,
        })

        pred_map = {}
        for q in result["by_question"]:
            qid = q["question_id"]
            pred_answer = (q.get("predicted_answer") or "").strip()
            if not pred_answer:
                pred_answer = "I don't know."
            pred_map[qid] = pred_answer

        pred_obj = {
            "answer": pred_map,
            "sp": {}
        }
        with open(os.path.join(PRED_DIR, f"preds_{user_name}.json"), "w", encoding="utf-8") as f:
            json.dump(pred_obj, f, indent=2, ensure_ascii=False)

    t_perm_check_end = time.time()
    print(f"\n[TIMER] Permission checking and prediction generation took: {t_perm_check_end - t_perm_check_start:.2f}s")

    t_eval_start = time.time()

    admin_file = None
    for fname in os.listdir(PRED_DIR):
        if fname.endswith(".json") and "baseline-admin" in fname:
            admin_file = os.path.join(PRED_DIR, fname)
            break

    for fname in sorted(os.listdir(PRED_DIR)):
        if not fname.endswith(".json"):
            continue
        pred_path = os.path.join(PRED_DIR, fname)
        base = os.path.splitext(fname)[0]
        user_from_file = base.replace("preds_", "", 1)

        try:
            metrics = hp_eval(
                prediction_file=pred_path,
                gold_file=GOLD_FILE,
                admin_prediction_file=admin_file
            )
            if user_from_file in summary_index:
                summary_index[user_from_file]["em"] = round(metrics.get("em", 0.0), 4)
                summary_index[user_from_file]["f1"] = round(metrics.get("f1", 0.0), 4)
                if "em_admin" in metrics:
                    summary_index[user_from_file]["em_admin"] = round(metrics["em_admin"], 4)

            print(f"[INFO] evaluated {fname} -> em={metrics.get('em'):.4f}, "
                  f"f1={metrics.get('f1'):.4f}, "
                  f"em_admin={metrics.get('em_admin', 0.0):.4f}")
        except Exception as e:
            print(f"[ERROR] Failed to evaluate {fname}: {e}")

    t_eval_end = time.time()
    print(f"[TIMER] Final evaluation (EM, F1, EM@Admin) took: {t_eval_end - t_eval_start:.2f}s\n")
    
    # ----- Save outputs -----
    quick = [
    {
        'user': r.get('user'),
        'PermCov': round(r.get('PermissionCoverage_pct', 0.0), 2),
        'Match': 'PASS' if r.get('AccessMatch') else 'FAIL',
        'EM': r.get('em'),
        'F1': r.get('f1'),
        'EM@Admin': r.get('em_admin')
    } for r in summary_rows
    ]
    print('[SUMMARY]', quick)
    
    with open(os.path.join(RESULTS_DIR, "quantitative_summary.json"), "w", encoding="utf-8") as f:
        json.dump(quick, f, indent=2, ensure_ascii=False)
    with open(os.path.join(RESULTS_DIR, "quantitative_details.json"), "w", encoding="utf-8") as f:
        json.dump(details_all, f, indent=2, ensure_ascii=False)

    # ----- Formatted Metrics Table (test_metrics.txt) -----
    total_time = round(t_eval_end - t_perm_check_start, 2)
    num_questions = len(qp.sampled_questions or qp.questions)
    metric_table_lines = [
        "=" * 105,
        "                    PERMISSION-AWARE RAG: EXPERIMENT TEST METRICS SUMMARY",
        "=" * 105,
        "Experiment Details:",
        f"  - Questions Tested:    {num_questions}",
        f"  - Top-k Retrieved:     {TOP_K}",
        f"  - Concurrency:         {QUESTION_CONCURRENCY} worker(s)",
        f"  - Target Users:        {', '.join(sorted(TARGET_USERS))}",
        f"  - Vector Store:        FAISS (BAAI/bge-base-en-v1.5, 768-dim)",
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
        "PERMISSION-AWARE RAG: DETAILED EVALUATION REPORT",
        "=" * 80,
        "",
    ]
    for item in details_all:
        uname = item["config"]["user"]
        result = item["result"]
        report_lines.append("=" * 80)
        report_lines.append(f"USER EVALUATION: {uname}")
        report_lines.append("=" * 80)
        report_lines.append("")

        for idx, q_info in enumerate(result.get("by_question", [])):
            qtext = q_info.get("question", "")
            pred_ans = q_info.get("predicted_answer", "")
            gold_ans = q_info.get("gold_answer", "")
            retrieved_docs = q_info.get("retrieved_docs", [])
            accessed_docs = q_info.get("accessed_docs", [])
            denied_docs = q_info.get("denied_docs", [])

            p_lower = pred_ans.strip().lower()
            g_lower = gold_ans.strip().lower()
            if g_lower in p_lower or p_lower in g_lower:
                match_str = "MATCH (CORRECT)"
            elif p_lower in ["i don't know.", "i do not know.", "i don't know", "error"]:
                match_str = "BLOCKED (ACCESS DENIED - ANSWER PREVENTED)"
            else:
                match_str = "MISMATCH"

            report_lines.append(f"[Question {idx + 1}]")
            report_lines.append(f"  QUESTION:                {qtext}")
            report_lines.append(f"  RETRIEVED DOCS ({len(retrieved_docs)}):")
            for rd in retrieved_docs:
                st = "ALLOWED" if rd in accessed_docs else "BLOCKED by IAM"
                report_lines.append(f"    - {rd} [{st}]")
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
    
    os.makedirs("results", exist_ok=True)
    with open("results/detailed_evaluation_report.txt", "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\n[INFO] Detailed evaluation report saved to: {rep_path}")
    print(f"[INFO] Also saved to: results/detailed_evaluation_report.txt")
    print("[DONE] Saved qualitative summary, details, and text report.")

if __name__ == "__main__":
    main()