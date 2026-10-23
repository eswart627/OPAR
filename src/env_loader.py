import os
from pathlib import Path
from dotenv import load_dotenv

# Look for .env in config/ directory first, then fallback to current working directory or repo root
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
ROOT_DIR = Path(__file__).resolve().parent.parent

if (CONFIG_DIR / ".env").exists():
    load_dotenv(dotenv_path=CONFIG_DIR / ".env", override=False)
if (ROOT_DIR / ".env").exists():
    load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)
