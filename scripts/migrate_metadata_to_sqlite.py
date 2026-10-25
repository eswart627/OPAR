# scripts/migrate_metadata_to_sqlite.py
"""
Migration Script: artifacts/metadata.json -> artifacts/metadata.db (SQLite)
Extracts records and stores them in a decoupled SQLite relational database.
Preserves full JSON fidelity and indexes global_uuid (PK), file_name, and bucket.
"""

import os
import sys
import json
import sqlite3
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def init_sqlite_db(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS metadata (
        global_uuid TEXT PRIMARY KEY,
        file_name TEXT NOT NULL,
        provider TEXT NOT NULL,
        bucket TEXT NOT NULL,
        region TEXT,
        endpoint TEXT,
        raw_json TEXT NOT NULL
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_file_name ON metadata(file_name);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bucket ON metadata(bucket);")
    conn.commit()
    return conn

def migrate(json_path: str, db_path: str) -> int:
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Source JSON file not found at: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        raise ValueError(f"Expected a list of metadata records in {json_path}")

    conn = init_sqlite_db(db_path)
    cur = conn.cursor()

    count = 0
    for r in records:
        uid = r.get("global_uuid")
        fname = r.get("file_name", "")
        provider = (r.get("provider") or "").lower()
        params = r.get("parameters") or {}
        bucket = params.get("bucket", "")
        region = params.get("region", "")
        endpoint = r.get("endpoint", "")
        raw_json = json.dumps(r, ensure_ascii=False)

        cur.execute("""
        INSERT OR REPLACE INTO metadata (
            global_uuid, file_name, provider, bucket, region, endpoint, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (uid, fname, provider, bucket, region, endpoint, raw_json))
        count += 1

    conn.commit()
    conn.close()
    return count

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate metadata.json to SQLite metadata.db")
    parser.add_argument("--input", default="artifacts/metadata.json", help="Input JSON file path")
    parser.add_argument("--output", default="artifacts/metadata.db", help="Output SQLite DB path")
    args = parser.parse_args()

    print(f"Starting migration from {args.input} -> {args.output}...")
    inserted = migrate(args.input, args.output)
    print(f"Successfully migrated {inserted} records into SQLite: {args.output}")
