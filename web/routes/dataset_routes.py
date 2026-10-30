# web/routes/dataset_routes.py
# Blueprint for dataset upload, validation, and dual-ingestion execution

import os
import sys
import json
import time
import logging
import subprocess
from flask import Blueprint, request, jsonify
from web.services.pipeline_service import PipelineService
from src.managers.ingestion_manager import IngestionManager

logger = logging.getLogger("RAG_Web_DatasetRoutes")
dataset_bp = Blueprint("dataset", __name__)

@dataset_bp.route("/upload-dataset", methods=["POST"])
@dataset_bp.route("/dataset/upload-dataset", methods=["POST"])
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
@dataset_bp.route("/dataset/ingest", methods=["POST"])
def run_ingest():
    service = PipelineService.get_instance()
    try:
        t0 = time.time()
        # Safely release open database handles before rebuilding
        if service.metadata_store and hasattr(service.metadata_store, "close"):
            service.metadata_store.close()

        import importlib
        access_model_path = os.path.join(service.artifacts_dir, "generated_access_model.json")
        storage_map_path = os.path.join(service.artifacts_dir, "file_to_storage_info.json")

        # 1. Access Model (Script 01)
        if not os.path.exists(access_model_path):
            logger.info("Executing Script 01: generate_access_model...")
            script_01 = os.path.join(service.project_root, "scripts", "01_generate_access_model.py")
            subprocess.run([sys.executable, "-u", script_01], cwd=service.project_root, check=True)

        # 2. Cloud Resource Creator (Script 02)
        try:
            auth_mode = os.environ.get("AUTH_MODE", "static").strip().lower()
            logger.info(f"Executing Script 02: gcp_aws_resource_creator with --auth_mode {auth_mode}...")
            script_02 = os.path.join(service.project_root, "scripts", "02_gcp_aws_resource_creator.py")
            if os.path.exists(script_02):
                subprocess.run(
                    [sys.executable, "-u", script_02, "--auth_mode", auth_mode],
                    cwd=service.project_root,
                    capture_output=True,
                    text=True,
                    timeout=90
                )
        except Exception as res_err:
            logger.warning(f"Script 02 cloud provisioning note: {res_err}")

        # 3. Document Preparation (Script 03)
        if not os.path.exists(storage_map_path):
            logger.info("Executing Script 03: prepare_documents...")
            prep_mod = importlib.import_module("scripts.03_prepare_documents")
            prep_mod.generate_documents_and_storage_info()

        # 4. Upload Resources to Cloud (Script 04)
        try:
            logger.info("Executing Script 04: upload_resources...")
            upload_mod = importlib.import_module("scripts.04_upload_resources")
            upload_mod.upload_all(max_workers=20)
        except Exception as up_err:
            logger.warning(f"Cloud upload warning (continuing with local ingestion): {up_err}")

        # 5. User Accessible File List (Script 05)
        logger.info("Executing Script 05: generate_user_accessible_file_list...")
        vis_mod = importlib.import_module("scripts.05_generate_user_accessible_file_list")
        vis_mod.generate_access_visibility()

        # 6. Question List (Script 06)
        logger.info("Executing Script 06: generate_question_list...")
        q_mod = importlib.import_module("scripts.06_generate_question_list")
        q_mod.generate_question_list()

        # 7. Dual FAISS + BM25 Ingestion Manager (Script 07)
        logger.info("Executing Script 07: run_ingestion_manager...")
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
            "message": f"Multi-Cloud Ingestion Pipeline (01-07) completed successfully in {elapsed}s",
            "elapsed_s": elapsed
        })
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        return jsonify({"error": str(e)}), 500


@dataset_bp.route("/teardown", methods=["POST"])
@dataset_bp.route("/dataset/teardown", methods=["POST"])
def run_teardown():
    """Executes the complete multi-cloud and local data teardown sequence."""
    service = PipelineService.get_instance()
    script_path = os.path.join(service.project_root, "scripts", "ops", "utils", "run_teardown_sequence.py")
    if not os.path.exists(script_path):
        return jsonify({"error": f"Teardown script not found: {script_path}"}), 500

    try:
        t0 = time.time()
        logger.info("Executing Complete Teardown Sequence (scripts/ops/utils/run_teardown_sequence.py)...")
        res = subprocess.run(
            [sys.executable, "-u", script_path],
            cwd=service.project_root,
            capture_output=True,
            text=True,
            timeout=180
        )
        elapsed = round(time.time() - t0, 2)
        # Reload pipeline service to reflect purged state
        service.reload()
        return jsonify({
            "success": True,
            "message": f"Teardown & Reset Sequence completed in {elapsed}s",
            "elapsed_s": elapsed,
            "output": res.stdout[-800:] if res.stdout else "",
            "returncode": res.returncode
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Teardown sequence timed out after 180s"}), 504
    except Exception as e:
        logger.error(f"Teardown exception: {e}")
        return jsonify({"error": str(e)}), 500


@dataset_bp.route("/storage-summary", methods=["GET"])
@dataset_bp.route("/dataset/storage-summary", methods=["GET"])
def get_storage_summary():
    """Returns real-time multi-cloud storage telemetry: AWS & GCP buckets and per-bucket file counts."""
    service = PipelineService.get_instance()
    access_model_path = os.path.join(service.artifacts_dir, "generated_access_model.json")
    storage_map_path = os.path.join(service.artifacts_dir, "file_to_storage_info.json")

    access_model = {}
    if os.path.exists(access_model_path):
        try:
            with open(access_model_path, "r", encoding="utf-8") as f:
                access_model = json.load(f)
        except Exception as e:
            logger.error(f"Error reading access model: {e}")

    storage_map = {}
    if os.path.exists(storage_map_path):
        try:
            with open(storage_map_path, "r", encoding="utf-8") as f:
                storage_map = json.load(f)
        except Exception as e:
            logger.error(f"Error reading storage map: {e}")

    # Count files per bucket
    bucket_counts = {}
    for doc_name, doc_info in storage_map.items():
        b = doc_info.get("bucket")
        if b:
            bucket_counts[b] = bucket_counts.get(b, 0) + 1

    # AWS Buckets
    aws_buckets = []
    aws_abac = access_model.get("AWS_ABAC", {})
    for b_name, attrs in aws_abac.items():
        attr_str = ", ".join(f"{k}={v}" for k, v in attrs.items())
        aws_buckets.append({
            "name": b_name,
            "policy": f"ABAC: {attr_str}" if attr_str else "ABAC",
            "file_count": bucket_counts.get(b_name, 0)
        })

    # GCP Buckets
    gcp_buckets = []
    gcp_rbac = access_model.get("GCP_RBAC", {})
    for role_name, b_name in gcp_rbac.items():
        gcp_buckets.append({
            "name": b_name,
            "policy": f"RBAC: {role_name}",
            "file_count": bucket_counts.get(b_name, 0)
        })

    total_aws_files = sum(b["file_count"] for b in aws_buckets)
    total_gcp_files = sum(b["file_count"] for b in gcp_buckets)

    return jsonify({
        "status": "success",
        "total_documents": len(storage_map),
        "aws": {
            "provider": "Amazon Web Services (S3)",
            "region": os.environ.get("AWS_REGION", "ap-south-1"),
            "total_files": total_aws_files,
            "buckets": aws_buckets
        },
        "gcp": {
            "provider": "Google Cloud Platform (GCS)",
            "region": os.environ.get("GCP_BUCKET_LOCATION", "asia-south1"),
            "total_files": total_gcp_files,
            "buckets": gcp_buckets
        }
    })
