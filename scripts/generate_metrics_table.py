import json
import os

summary_path = os.path.join("results", "quantitative_summary.json")
details_path = os.path.join("results", "quantitative_details.json")

if os.path.exists(summary_path) and os.path.exists(details_path):
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
    with open(details_path, "r", encoding="utf-8") as f:
        details = json.load(f)

    total_q = len(details[0]["result"]["by_question"]) if details else 0

    lines = [
        "=" * 105,
        "                    PERMISSION-AWARE RAG: EXPERIMENT TEST METRICS SUMMARY",
        "=" * 105,
        "Experiment Configuration & Environment:",
        f"  - Questions Evaluated: {total_q}",
        "  - Top-k Retrieved:     5 documents per query",
        "  - Evaluated Users:     user-1, user-3, baseline-admin",
        "  - Vector Store:        FAISS (all-MiniLM-L6-v2, 384 dimensions)",
        "  - LLM Backend:         Dynamic Fallback Engine (Gemini 2.5 Flash / Pro / Flash-Lite)",
        "  - Multi-Cloud Storage: 5 AWS S3 Buckets, 5 GCP GCS Buckets (Total 13,783 documents)",
        "-" * 105,
        f"{'User ID':<16} | {'PermCov (%)':<12} | {'Match':<7} | {'Exact Match':<12} | {'F1 Score':<10} | {'EM@Admin':<10} | {'Docs Auth / Retr':<16}",
        "-" * 105,
    ]

    for s, d in zip(summary, details):
        uname = s.get("user", "")
        pc = f"{s.get('PermCov', 0.0):.2f}%"
        m = s.get("Match", "FAIL")
        em = f"{s.get('EM', 0.0):.4f}" if s.get("EM") is not None else "N/A"
        f1 = f"{s.get('F1', 0.0):.4f}" if s.get("F1") is not None else "N/A"
        ema = f"{s.get('EM@Admin', 0.0):.4f}" if s.get("EM@Admin") is not None else "N/A"
        agg = d.get("result", {}).get("aggregate", {})
        docs = f"{agg.get('total_authorized', 0)} / {agg.get('total_retrieved', 0)}"
        lines.append(f"{uname:<16} | {pc:<12} | {m:<7} | {em:<12} | {f1:<10} | {ema:<10} | {docs:<16}")

    lines.extend([
        "-" * 105,
        "IEEE Access Metric Definitions:",
        "  * PermCov (%):  Permission Coverage ratio of authorized documents (|D_auth| / |D_retr| * 100).",
        "  * Match:        PASS if no access leakage (FP=0) and no authorized files wrongfully blocked (FN=0).",
        "  * Exact Match:  Token exact match between LLM predicted answer and golden reference answer.",
        "  * F1 Score:     Harmonic mean of token precision and recall against golden reference answer.",
        "  * EM@Admin:     Exact Match accuracy on questions correctly answered by baseline-admin.",
        "=" * 105,
        ""
    ])

    out_content = "\n".join(lines)
    with open(os.path.join("results", "test_metrics.txt"), "w", encoding="utf-8") as f:
        f.write(out_content)
    print("test_metrics.txt created successfully.")
