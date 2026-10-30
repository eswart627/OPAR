# web/app.py
# Application factory creating and configuring the modular Flask application

import os
from flask import Flask, send_from_directory
from web.routes.status_routes import status_bp
from web.routes.dataset_routes import dataset_bp
from web.routes.query_routes import query_bp
from web.routes.metrics_routes import metrics_bp
from web.routes.config_routes import config_bp

def create_app() -> Flask:
    static_folder = os.path.join(os.path.dirname(__file__), "static")
    app = Flask(__name__, static_folder=static_folder)

    # Register modular blueprints under /api prefix
    app.register_blueprint(status_bp, url_prefix="/api")
    app.register_blueprint(dataset_bp, url_prefix="/api")
    app.register_blueprint(query_bp, url_prefix="/api")
    app.register_blueprint(metrics_bp, url_prefix="/api")
    app.register_blueprint(config_bp, url_prefix="/api/config")

    @app.route("/")
    def index():
        return send_from_directory(static_folder, "index.html")

    @app.route("/<path:path>")
    def serve_static(path):
        full_path = os.path.join(static_folder, path)
        if os.path.exists(full_path):
            return send_from_directory(static_folder, path)
        return send_from_directory(static_folder, "index.html")

    return app
