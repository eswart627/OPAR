# scripts/ops/clean_generated_data.py
# Purpose: Resets the pipeline data by removing all generated .json and .txt files
# and wiping vector_db indices, while strictly preserving dataset files and GCP admin keys.

import os
import sys
import shutil
import glob
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Protected patterns (NEVER DELETE THESE)
PROTECTED_PATTERNS = [
    "dataset",
    "hotpot",
    "gcp_admin",
    "gcp-admin",
    "gcp_admin_key",
    ".env",
    "teardown_sequence",
]

def is_protected(file_name: str) -> bool:
    """Returns True if the file matches any protected keyword."""
    name_lower = file_name.lower()
    for pat in PROTECTED_PATTERNS:
        if pat in name_lower:
            return True
    return False

def clean_generated_data(clean_sqlite: bool = True):
    print("=" * 75)
    print(">>> PIPELINE DATA RESET: CLEANING GENERATED JSON, TXT & VECTOR INDICES")
    print("=" * 75)

    deleted_count = 0
    preserved_count = 0

    # 1. Clean generated files in artifacts/
    artifacts_dir = os.path.join(project_root, "artifacts")
    if os.path.exists(artifacts_dir):
        print(f"\n[1] Scanning artifacts directory: {artifacts_dir}...")
        for root, dirs, files in os.walk(artifacts_dir, topdown=False):
            for f in files:
                fpath = os.path.join(root, f)
                ext = os.path.splitext(f)[1].lower()

                if ext in [".json", ".txt"]:
                    if is_protected(f):
                        print(f"    [PRESERVED] Protected dataset/key: {f}")
                        preserved_count += 1
                    else:
                        try:
                            os.remove(fpath)
                            print(f"    [DELETED] {os.path.relpath(fpath, project_root)}")
                            deleted_count += 1
                        except Exception as e:
                            print(f"    [ERROR] Could not delete {fpath}: {e}")

            # Clean empty subdirectories in artifacts (like artifacts/resources/AWS/...)
            for d in dirs:
                dpath = os.path.join(root, d)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except Exception:
                    pass

        # Optional: reset SQLite metadata.db
        db_path = os.path.join(artifacts_dir, "metadata.db")
        if clean_sqlite and os.path.exists(db_path):
            try:
                os.remove(db_path)
                print(f"    [DELETED] artifacts/metadata.db (SQLite database reset)")
                deleted_count += 1
            except Exception as e:
                print(f"    [WARN] Could not remove metadata.db: {e}")

    # 2. Clean generated evaluation files in results/
    results_dir = os.path.join(project_root, "results")
    if os.path.exists(results_dir):
        print(f"\n[2] Scanning results directory: {results_dir}...")
        for root, dirs, files in os.walk(results_dir, topdown=False):
            for f in files:
                fpath = os.path.join(root, f)
                ext = os.path.splitext(f)[1].lower()
                if ext in [".json", ".txt"]:
                    if is_protected(f):
                        print(f"    [PRESERVED] {f}")
                        preserved_count += 1
                    else:
                        try:
                            os.remove(fpath)
                            print(f"    [DELETED] {os.path.relpath(fpath, project_root)}")
                            deleted_count += 1
                        except Exception as e:
                            print(f"    [ERROR] Could not delete {fpath}: {e}")

            for d in dirs:
                dpath = os.path.join(root, d)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except Exception:
                    pass

    # 3. Wipe vector_db/ directory (FAISS, BM25 indices, metadata)
    vector_db_dir = os.path.join(project_root, "vector_db")
    print(f"\n[3] Cleaning vector database directory: {vector_db_dir}...")
    if os.path.exists(vector_db_dir):
        for f in os.listdir(vector_db_dir):
            fpath = os.path.join(vector_db_dir, f)
            if is_protected(f):
                print(f"    [PRESERVED] {f}")
                preserved_count += 1
            else:
                try:
                    if os.path.isfile(fpath) or os.path.islink(fpath):
                        os.remove(fpath)
                        print(f"    [DELETED] vector_db/{f}")
                        deleted_count += 1
                    elif os.path.isdir(fpath):
                        shutil.rmtree(fpath)
                        print(f"    [DELETED] vector_db/{f}/ (directory)")
                        deleted_count += 1
                except Exception as e:
                    print(f"    [ERROR] Could not delete {fpath}: {e}")
    else:
        print("    [INFO] vector_db directory does not exist.")

    # 4. Check config/ directory to ensure admin keys remain untouched
    config_dir = os.path.join(project_root, "config")
    print(f"\n[4] Checking config directory: {config_dir}...")
    if os.path.exists(config_dir):
        for f in os.listdir(config_dir):
            if is_protected(f):
                print(f"    [PRESERVED] Critical Admin Key / Env: {f}")
                preserved_count += 1

    print("\n" + "=" * 75)
    print(f"  CLEANUP COMPLETE: {deleted_count} files removed, {preserved_count} protected files preserved.")
    print("=" * 75 + "\n")

if __name__ == "__main__":
    clean_generated_data()
