import os
from pathlib import Path
from dotenv import load_dotenv

# Canonical location: config/.env
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
ROOT_DIR = Path(__file__).resolve().parent.parent

def load_project_env():
    config_env = CONFIG_DIR / ".env"
    root_env = ROOT_DIR / ".env"
    if config_env.exists():
        load_dotenv(dotenv_path=config_env, override=True)
    elif root_env.exists():
        load_dotenv(dotenv_path=root_env, override=True)

load_project_env()

