# web/routes/dataset_routes.py
# Blueprint for dataset upload, validation, and dual-ingestion execution

import os
import json
import time
import logging
from flask import Blueprint, request, jsonify
from web.services.pipeline_service import PipelineService
from src.managers.ingestion_manager import IngestionManager

logger = logging.getLogger("RAG_Web_DatasetRoutes")
dataset_bp = Blueprint("dataset", __name__)

@dataset_bp.route("/upload-dataset", methods=["POST"])
def upload_dataset():
    service = PipelineService.get_instance()
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded in form-data"}), 400
        
        file = request.files["file"]
        if not file.filename.endswith(".json"):
            return jsonify({"error": "Invalid format. Only .json HotpotQA benchmark files are accepted."}), 400

        content = json.load(file.stream)
        if not isinstance(content, list) or len(content) == 0:
            return jsonify({"error": "Dataset must be a non-empty JSON list."}), 400

        sample_item = content[0]
        required_keys = ["_id", "question", "answer", "context", "supporting_facts"]
        missing = [k for k in required_keys if k not in sample_item]
        if missing:
            return jsonify({
                "error": f"Invalid HotpotQA benchmark schema. Missing required keys: {', '.join(missing)}"
            }), 400

        target_path = os.path.join(service.artifacts_dir, "hotpot_dev_distractor_v1.json")
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2, ensure_ascii=False)

        # Automatically extract fresh question list
        import importlib
        gen_mod = importlib.import_module("scripts.06_generate_question_list")
        gen_mod.generate_question_list()

        return jsonify({
            "success": True,
            "message": f"Dataset verified and uploaded successfully ({len(content)} questions).",
            "question_count": len(content)
        })
    except Exception as e:
        logger.error(f"Dataset upload exception: {e}")
        return jsonify({"error": str(e)}), 500

@dataset_bp.route("/ingest", methods=["POST"])
def run_ingest():
    service = PipelineService.get_instance()
    try:
        t0 = time.time()
        import importlib
        storage_map_path = os.path.join(service.artifacts_dir, "file_to_storage_info.json")
        
        # 1. Prepare documents and multi-cloud storage mapping if missing
        if not os.path.exists(storage_map_path):
            prep_mod = importlib.import_module("scripts.03_prepare_documents")
            prep_mod.generate_documents_and_storage_info()
            
            vis_mod = importlib.import_module("scripts.05_generate_user_accessible_file_list")
            vis_mod.generate_access_visibility()

            # Upload to cloud buckets
            try:
                upload_mod = importlib.import_module("scripts.04_upload_resources")
                upload_mod.upload_all(max_workers=20)
            except Exception as up_err:
                logger.warning(f"Cloud upload warning (continuing with local ingestion): {up_err}")

        # 2. Run dual FAISS + BM25 ingestion manager
        from src.managers.ingestion_manager import IngestionManager, IngestionConfig
        cfg = IngestionConfig(
            hotpot_file_path=os.path.join(service.artifacts_dir, "hotpot_dev_distractor_v1.json"),
            storage_map_path=storage_map_path,
            output_dir=service.artifacts_dir,
            vector_db_dir=service.vector_db_dir
        )
        im = IngestionManager(cfg)
        im.ingest()
        service.reload()
        elapsed = round(time.time() - t0, 2)
        return jsonify({
            "success": True,
            "message": f"Dual-Ingestion completed successfully in {elapsed}s",
            "elapsed_s": elapsed
        })
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        return jsonify({"error": str(e)}), 500
