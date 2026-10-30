# web/routes/query_routes.py
# Blueprint for interactive multi-hop querying, security filtering, and grounded synthesis

import time
import logging
from flask import Blueprint, request, jsonify
from web.services.pipeline_service import PipelineService
from web.services.evaluator_service import EvaluatorService

logger = logging.getLogger("RAG_Web_QueryRoutes")
query_bp = Blueprint("query", __name__)

@query_bp.route("/query", methods=["POST"])
def run_query():
    service = PipelineService.get_instance()
    if not service.is_ready:
        service.reload()
        if not service.is_ready:
            return jsonify({
                "error": "Pipeline components not initialized. Please ensure indices are built."
            }), 400

    payload = request.get_json() or {}
    user_name = payload.get("user", "user-1")
    mode = payload.get("mode", "hybrid").lower()
    top_k = int(payload.get("top_k", 5))
    qtext = payload.get("question", "").strip()
    gold_answer = payload.get("gold_answer", "")
    gold_sup_docs = payload.get("supporting_facts", [])

    if not qtext:
        return jsonify({"error": "Empty query received."}), 400

    retriever = service.active_retrievers.get(mode) or service.active_retrievers.get("faiss")
    if not retriever:
        return jsonify({"error": f"Retriever mode '{mode}' is unavailable."}), 400

    t_start = time.time()

    # 1. Retrieval
    t0 = time.time()
    retrieved = retriever.search(qtext, top_k=top_k)
    t_retrieval_ms = round((time.time() - t0) * 1000, 2)

    # 2. Authorization & IAM Inspection
    t1 = time.time()
    ac_manager = service.user_manager.get_ac_manager(user_name)
    filtered, decisions, per_uuid_dur, _ = service.permission_retriever.filter_docs(retrieved, ac_manager)
    t_auth_ms = round((time.time() - t1) * 1000, 2)

    cache_enabled = bool(getattr(service.permission_retriever.config, "use_cache", False)) if service.permission_retriever else False
    hits_in_query = sum(1 for d in per_uuid_dur.values() if d < 0.001) if cache_enabled else 0
    misses_in_query = len(per_uuid_dur) - hits_in_query if cache_enabled else len(per_uuid_dur)
    is_cache_hit = (hits_in_query > 0 and misses_in_query == 0) if (cache_enabled and per_uuid_dur) else False

    # Build document attribution ledger
    doc_results = []
    uuid_to_meta = service.question_processor.uuid_to_metadata

    for row in retrieved:
        uid = row.get("global_uuid")
        meta = uuid_to_meta.get(uid, {})
        fname = meta.get("file_name", uid)
        
        # Extract bucket and provider
        params = meta.get("parameters", {})
        bucket = params.get("bucket") or meta.get("storage_identifier", "Unknown Bucket")
        provider = meta.get("provider", "cloud").upper()
        
        is_allowed = bool(decisions.get(uid, False))

        is_supporting = False
        for sup in gold_sup_docs:
            if sup.replace(".txt", "").lower() in fname.replace(".txt", "").lower():
                is_supporting = True
                break

        # Precise mode-aware score extraction
        if mode == "hybrid":
            score_val = float(row.get("rrf_score", row.get("score", 0.0)))
            cap = 0.0328
            rel_pct = min(100.0, max(0.0, (score_val / cap) * 100.0))
            score_mode_label = "Hybrid RRF"
            cap_label = "0.0328 (Max Consensus)"
        else:
            # FAISS IndexFlatL2 returns squared Euclidean distance (L2 distance): 0.0 = identical
            dist = float(row.get("distance", row.get("score", 0.0)))
            score_val = dist
            # Cosine / proximity similarity equivalent: 1 / (1 + dist) bounded strictly in (0.0, 1.0]
            sim = 1.0 / (1.0 + dist)
            rel_pct = min(100.0, max(0.0, sim * 100.0))
            score_mode_label = f"FAISS L2 Dist: {round(dist, 3)} (Sim: {round(sim, 2)})"
            cap_label = "0.000 (Exact Match)"

        doc_results.append({
            "global_uuid": uid,
            "file_name": fname,
            "bucket": f"{bucket} ({provider})" if bucket != "Unknown Bucket" else "Unknown Bucket",
            "is_allowed": is_allowed,
            "is_supporting_doc": is_supporting,
            "score": round(score_val, 4),
            "score_pct": round(rel_pct, 1),
            "score_mode": score_mode_label,
            "score_cap": cap_label,
            "content_snippet": (row.get("content", "") or "")[:280] + "..."
        })

    # 3. Grounded LLM Generation
    t2 = time.time()
    context_contents = [f.get("content", "") for f in filtered if f.get("content")]

    if not context_contents:
        predicted_answer = "I don't know."
    else:
        try:
            context_string = "\n\n".join(context_contents)
            out = service.question_processor.qa_chain.invoke({
                "context": context_string,
                "question": qtext
            })
            predicted_answer = (out or "").strip() or "I don't know."
        except Exception as e:
            logger.error(f"LLM Synthesis error: {e}")
            predicted_answer = "I don't know."

    t_gen_ms = round((time.time() - t2) * 1000, 2)
    t_total_ms = round((time.time() - t_start) * 1000, 2)

    grounding_status = "BLOCKED_ACCESS_DENIED" if not context_contents or predicted_answer == "I don't know." else "ALLOWED_GROUNDED"
    metrics = EvaluatorService.evaluate(predicted_answer, gold_answer)

    return jsonify({
        "question": qtext,
        "user": user_name,
        "mode": mode,
        "score_mode": "Hybrid RRF" if mode == "hybrid" else "FAISS Cosine",
        "score_cap": 0.0328 if mode == "hybrid" else 1.0,
        "predicted_answer": predicted_answer,
        "gold_answer": gold_answer,
        "grounding_status": grounding_status,
        "documents": doc_results,
        "allowed_count": len(filtered),
        "total_retrieved": len(retrieved),
        "metrics": metrics,
        "cache_verdict": {
            "enabled": cache_enabled,
            "hit": is_cache_hit,
            "hits": hits_in_query,
            "misses": misses_in_query,
            "latency_ms": t_auth_ms
        },
        "latencies": {
            "retrieval_ms": t_retrieval_ms,
            "authorization_ms": t_auth_ms,
            "generation_ms": t_gen_ms,
            "total_ms": t_total_ms
        }
    })
