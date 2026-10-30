# web/routes/config_routes.py
# Blueprint for interactive pipeline operations: drift queues, live listener daemon,
# in-memory decision cache, and identity mode switching.

import os
import logging
from flask import Blueprint, jsonify, request
from web.services.pipeline_service import PipelineService
from web.services.drift_control_service import DriftControlService

logger = logging.getLogger("RAG_Web_ConfigRoutes")
config_bp = Blueprint("config", __name__)


@config_bp.route("/state", methods=["GET"])
def get_config_state():
    """Returns aggregated real-time operational state across queues, listener, cache, and auth mode."""
    drift_service = DriftControlService.get_instance()
    pipeline_service = PipelineService.get_instance()

    # Queue status
    queue_status = drift_service.inspect_queue_status()

    # Listener status
    listener_running = drift_service.is_listener_running()
    listener_pid = drift_service.listener_process.pid if listener_running else None
    listener_provider = drift_service.listener_provider if listener_running else "none"

    # Cache status
    cache_enabled = getattr(pipeline_service, "cache_enabled", False)
    cache_stats = {"hits": 0, "misses": 0, "hit_ratio_pct": 0.0, "size": 0, "avg_cached_ms": 0.05}
    if pipeline_service.permission_retriever:
        if hasattr(pipeline_service.permission_retriever, "config"):
            cache_enabled = getattr(pipeline_service.permission_retriever.config, "use_cache", cache_enabled)
        if hasattr(pipeline_service.permission_retriever, "get_cache_stats"):
            cache_stats = pipeline_service.permission_retriever.get_cache_stats()

    # Auth mode
    auth_mode = os.environ.get("AUTH_MODE", "static").strip().lower()

    return jsonify({
        "status": "success",
        "auth_mode": auth_mode,
        "cache": {
            "enabled": cache_enabled,
            "stats": cache_stats,
        },
        "queues": queue_status,
        "listener": {
            "running": listener_running,
            "pid": listener_pid,
            "provider": listener_provider,
        },
    })


@config_bp.route("/drift-queue", methods=["POST"])
def toggle_drift_queue():
    """
    Provisions or tears down drift notification queues on the fly.
    Payload: {"action": "setup" | "teardown", "provider": "all" | "aws" | "gcp"}
    """
    drift_service = DriftControlService.get_instance()
    payload = request.get_json() or {}
    action = payload.get("action", "setup").strip().lower()
    provider = payload.get("provider", "all").strip().lower()

    if action == "setup":
        result = drift_service.setup_queues(provider=provider)
        queue_status = drift_service.inspect_queue_status()
        return jsonify({
            "status": "success",
            "action": "setup",
            "provider": provider,
            "details": result,
            "queues": queue_status,
        })
    elif action == "teardown":
        result = drift_service.teardown_queues(provider=provider)
        queue_status = drift_service.inspect_queue_status()
        return jsonify({
            "status": "success",
            "action": "teardown",
            "provider": provider,
            "details": result,
            "queues": queue_status,
        })
    else:
        return jsonify({"error": f"Invalid action: {action}. Expected 'setup' or 'teardown'."}), 400


@config_bp.route("/listener", methods=["POST"])
def toggle_listener():
    """
    Starts or stops the live drift listener daemon.
    Payload: {"action": "start" | "stop", "provider": "all" | "aws" | "gcp"}
    """
    drift_service = DriftControlService.get_instance()
    payload = request.get_json() or {}
    action = payload.get("action", "start").strip().lower()
    provider = payload.get("provider", "all").strip().lower()

    if action == "start":
        res = drift_service.start_listener(provider=provider)
        return jsonify({"status": "success", "action": "start", "listener": res})
    elif action == "stop":
        res = drift_service.stop_listener()
        return jsonify({"status": "success", "action": "stop", "listener": res})
    else:
        return jsonify({"error": f"Invalid action: {action}. Expected 'start' or 'stop'."}), 400


@config_bp.route("/cache", methods=["POST"])
def toggle_cache():
    """
    Enables or disables the in-memory decision cache dynamically.
    Payload: {"enabled": true | false}
    """
    pipeline_service = PipelineService.get_instance()
    payload = request.get_json() or {}

    if "enabled" not in payload:
        return jsonify({"error": "Missing 'enabled' boolean in payload"}), 400

    enabled = bool(payload["enabled"])
    pipeline_service.cache_enabled = enabled
    if pipeline_service.permission_retriever and hasattr(pipeline_service.permission_retriever, "config"):
        pipeline_service.permission_retriever.config.use_cache = enabled

    # Also log into drift control service log
    DriftControlService.get_instance()._add_log("CACHE", f"In-memory permission cache toggled: {'ENABLED' if enabled else 'DISABLED'}")

    return jsonify({
        "status": "success",
        "cache_enabled": enabled,
        "message": f"Permission cache successfully {'enabled' if enabled else 'disabled'}."
    })


@config_bp.route("/cache/invalidate", methods=["POST"])
def invalidate_cache():
    """
    Invalidates in-memory cache entries.
    Payload: {"user": "user-1"} to evict a specific user, or {} / {"all": true} to flush all.
    """
    pipeline_service = PipelineService.get_instance()
    retriever = pipeline_service.permission_retriever
    drift_service = DriftControlService.get_instance()

    payload = request.get_json() or {}
    user = payload.get("user")

    if retriever:
        if user:
            purged = retriever.invalidate_user(user)
            drift_service._add_log("CACHE", f"Evicted cached decisions for persona '{user}' ({purged} entries purged).")
        else:
            purged = retriever.clear_cache()
            drift_service._add_log("CACHE", f"Flushed all cached permission decisions ({purged} entries purged).")
    else:
        purged = 0
        if user:
            drift_service._add_log("CACHE", f"Evicted cached decisions for persona '{user}' (retriever idle).")
        else:
            drift_service._add_log("CACHE", "Flushed all cached permission decisions (retriever idle).")

    return jsonify({
        "status": "success",
        "invalidated_count": purged,
        "purged_count": purged,
        "user": user,
    })


@config_bp.route("/idp-mode", methods=["POST"])
def switch_idp_mode():
    """
    Switches active AUTH_MODE between 'static' and 'auth0' without restarting the server.
    Payload: {"mode": "static" | "auth0"}
    """
    pipeline_service = PipelineService.get_instance()
    payload = request.get_json() or {}
    mode = payload.get("mode", "").strip().lower()

    if mode not in ("static", "auth0"):
        return jsonify({"error": "Invalid mode. Expected 'static' or 'auth0'."}), 400

    os.environ["AUTH_MODE"] = mode
    # Reload credential manager in PipelineService
    try:
        from src.managers.credential_manager import CredentialManager
        pipeline_service.user_manager = CredentialManager(
            base_path=pipeline_service.artifacts_dir,
            file_name="test_credential.txt"
        )
        DriftControlService.get_instance()._add_log("AUTH", f"Identity provider switched to [{mode.upper()}]. User manager reloaded.")
        return jsonify({
            "status": "success",
            "auth_mode": mode,
            "message": f"Switched to {mode.upper()} mode. Active users loaded: {len(pipeline_service.user_manager.users)}"
        })
    except Exception as e:
        logger.error(f"Error reloading user manager in {mode} mode: {e}")
        return jsonify({"status": "partial", "auth_mode": mode, "warning": str(e)})


@config_bp.route("/logs", methods=["GET"])
def get_listener_logs():
    """Streams circular memory buffer logs for the live terminal console."""
    drift_service = DriftControlService.get_instance()
    since_id = request.args.get("since_id", 0, type=int)
    logs = drift_service.get_logs(since_id=since_id)
    return jsonify({
        "status": "success",
        "count": len(logs),
        "logs": logs,
        "last_id": drift_service._log_counter,
    })


@config_bp.route("/logs/clear", methods=["POST"])
def clear_listener_logs():
    """Clears the live terminal console buffer."""
    drift_service = DriftControlService.get_instance()
    last_id = drift_service.clear_logs()
    return jsonify({
        "status": "success",
        "message": "Log buffer cleared",
        "last_id": last_id,
    })
