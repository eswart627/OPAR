# web/routes/status_routes.py
# Blueprint for system health, cloud state, and active dataset status

import os
import json
import logging
from flask import Blueprint, jsonify
from web.services.pipeline_service import PipelineService

logger = logging.getLogger("RAG_Web_StatusRoutes")
status_bp = Blueprint("status", __name__)

@status_bp.route("/status", methods=["GET"])
def get_status():
    service = PipelineService.get_instance()
    gold_path = os.path.join(service.artifacts_dir, "hotpot_dev_distractor_v1.json")
    dataset_info = {"loaded": False, "question_count": 0, "file_name": "None", "questions": []}

    if os.path.exists(gold_path):
        try:
            with open(gold_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            questions = []
            for item in data:
                raw_sup = item.get("supporting_facts", [])
                sup_files = []
                for sf in raw_sup:
                    val = sf[0] if isinstance(sf, (list, tuple)) and len(sf) > 0 else sf
                    sval = str(val)
                    if not sval.endswith(".txt"):
                        sval = f"{sval}.txt"
                    sup_files.append(sval)
                questions.append({
                    "id": item.get("_id"),
                    "question": item.get("question"),
                    "gold_answer": item.get("answer"),
                    "supporting_facts": sup_files
                })
            dataset_info = {
                "loaded": True,
                "file_name": "hotpot_dev_distractor_v1.json",
                "question_count": len(data),
                "questions": questions
            }
        except Exception as e:
            logger.error(f"Error reading dataset: {e}")

    indices = {
        "faiss": os.path.exists(os.path.join(service.vector_db_dir, "faiss.index")),
        "bm25": os.path.exists(os.path.join(service.vector_db_dir, "bm25.pkl")),
        "metadata": os.path.exists(os.path.join(service.artifacts_dir, "metadata.json")),
    }

    return jsonify({
        "status": "online",
        "is_ready": service.is_ready,
        "dataset": dataset_info,
        "indices": indices,
        "supported_modes": ["hybrid", "faiss"],
        "users": [
            {"id": "user-1", "name": "Restricted Persona", "clearance": "0% Access", "badge_class": "badge-danger", "desc": "All Cloud Storage Blocked"},
            {"id": "user-3", "name": "Regional Persona", "clearance": "16% Access", "badge_class": "badge-warning", "desc": "Partial S3/GCP Bucket Access"},
            {"id": "baseline-admin", "name": "Enterprise Admin", "clearance": "100% Access", "badge_class": "badge-success", "desc": "Full Unrestricted Access"},
        ]
    })
