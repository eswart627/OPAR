# src/stores/metadata_store.py
import os
import json
import sqlite3
import threading
from typing import Dict, Any, List, Optional

class MetadataStore:
    """
    Decoupled Metadata Store backed strictly by SQLite.
    
    Provides:
    - O(1) B-tree indexed lookups by global_uuid and file_name.
    - Atomic single-row location/bucket updates without rewriting vector embeddings or JSON.
    - Zero dependency on metadata.json.
    """
    def __init__(self):
        self._db_path: Optional[str] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        
        # In-memory fast cache for parsed records
        self._by_uuid: Dict[str, Dict[str, Any]] = {}
        self._by_file: Dict[str, Dict[str, Any]] = {}
        self._all: List[Dict[str, Any]] = []

    def is_sqlite(self) -> bool:
        return self._conn is not None

    def load(self, metadata_path: str = "artifacts/metadata.db") -> None:
        """
        Loads metadata strictly from SQLite database (artifacts/metadata.db).
        """
        db_path = metadata_path
        if not (db_path.endswith(".db") or db_path.endswith(".sqlite")):
            db_path = os.path.splitext(metadata_path)[0] + ".db"

        if not os.path.exists(db_path):
            raise FileNotFoundError(f"SQLite metadata database not found at: {db_path}")

        self._init_sqlite(db_path)

    def _init_sqlite(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._refresh_cache_from_sqlite()

    def _refresh_cache_from_sqlite(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT raw_json FROM metadata")
            rows = cur.fetchall()
            
            all_records = []
            by_uuid = {}
            by_file = {}
            
            for (raw_str,) in rows:
                rec = json.loads(raw_str)
                all_records.append(rec)
                uid = rec.get("global_uuid")
                if uid:
                    by_uuid[uid] = rec
                fname = rec.get("file_name")
                if fname:
                    by_file[fname] = rec

            self._all = all_records
            self._by_uuid = by_uuid
            self._by_file = by_file

    def get_by_uuid(self, uuid: str) -> Optional[Dict[str, Any]]:
        if not uuid:
            return None
        with self._lock:
            # Fast path: check in-memory cache
            if uuid in self._by_uuid:
                return self._by_uuid[uuid]
            
            # Fallback to direct DB query if present
            if self._conn:
                cur = self._conn.cursor()
                cur.execute("SELECT raw_json FROM metadata WHERE global_uuid = ?", (uuid,))
                row = cur.fetchone()
                if row:
                    rec = json.loads(row[0])
                    self._by_uuid[uuid] = rec
                    return rec
            return None

    def get_by_file(self, file_name: str) -> Optional[Dict[str, Any]]:
        if not file_name:
            return None
        with self._lock:
            if file_name in self._by_file:
                return self._by_file[file_name]
            
            if self._conn:
                cur = self._conn.cursor()
                cur.execute("SELECT raw_json FROM metadata WHERE file_name = ?", (file_name,))
                row = cur.fetchone()
                if row:
                    rec = json.loads(row[0])
                    self._by_file[file_name] = rec
                    return rec
            return None

    def update_location(
        self,
        uuid: str,
        new_bucket: Optional[str] = None,
        new_file_name: Optional[str] = None,
        new_provider: Optional[str] = None
    ) -> bool:
        """
        Decoupled single-row atomic update:
        Updates the physical cloud pointer (bucket/path/provider) for a UUID in SQLite
        WITHOUT requiring any vector re-embedding or JSON file rewriting.
        """
        rec = self.get_by_uuid(uuid)
        if not rec:
            return False

        # Apply changes to record copy
        updated = json.loads(json.dumps(rec))
        if new_bucket is not None:
            if "parameters" not in updated:
                updated["parameters"] = {}
            updated["parameters"]["bucket"] = new_bucket
        if new_file_name is not None:
            updated["file_name"] = new_file_name
        if new_provider is not None:
            updated["provider"] = new_provider

        raw_str = json.dumps(updated, ensure_ascii=False)
        bucket_val = (updated.get("parameters") or {}).get("bucket", "")
        file_val = updated.get("file_name", "")
        prov_val = updated.get("provider", "")

        with self._lock:
            if self._conn:
                cur = self._conn.cursor()
                cur.execute("""
                UPDATE metadata 
                SET bucket = ?, file_name = ?, provider = ?, raw_json = ?
                WHERE global_uuid = ?
                """, (bucket_val, file_val, prov_val, raw_str, uuid))
                self._conn.commit()

            # Refresh local dictionaries
            self._by_uuid[uuid] = updated
            if file_val:
                self._by_file[file_val] = updated
            self._all = [r if r.get("global_uuid") != uuid else updated for r in self._all]

        return True

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._all)
