# app.py
# Root launcher for Permission-Aware RAG Web Application

import os
from web.app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n========================================================")
    print(f"  PERMISSION-AWARE RAG: HCI WEB INTERFACE RUNNING")
    print(f"  Local URL: http://127.0.0.1:{port}")
    print(f"========================================================\n")
    app.run(host="0.0.0.0", port=port, debug=False)
