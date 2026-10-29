# web/routes/metrics_routes.py
# Blueprint for quantitative evaluation benchmark summaries and confusion matrices

import os
import json
import logging
from flask import Blueprint, jsonify
from web.services.pipeline_service import PipelineService

logger = logging.getLogger("RAG_Web_MetricsRoutes")
metrics_bp = Blueprint("metrics", __name__)

@metrics_bp.route("/metrics", methods=["GET"])
def get_metrics():
    service = PipelineService.get_instance()
    summary_data = {}
    details_data = {}

    for mode in ["faiss", "hybrid"]:
        summary_path = os.path.join(service.results_dir, mode, "quantitative_summary.json")
        details_path = os.path.join(service.results_dir, mode, "quantitative_details.json")

        if os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as f:
                summary_data[mode] = json.load(f)
        if os.path.exists(details_path):
            with open(details_path, "r", encoding="utf-8") as f:
                details_data[mode] = json.load(f)

    return jsonify({
        "summary": summary_data,
        "details": details_data
    })
