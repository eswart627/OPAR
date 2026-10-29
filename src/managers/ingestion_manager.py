import os
import json
import uuid
import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple

from tqdm import tqdm
from src.stores.vector_store import VectorStore
from src.stores.bm25_store import BM25Store

def sanitize_filename(title: str) -> str:
    title = title.lower().replace(" ", "_")
    return re.sub(r"[^\w\d_]", "", title)

@dataclass
class IngestionConfig:
    storage_map_path: str = "file_to_storage_info.json"
    hotpot_file_path: str = "hotpot_dev_distractor_v1.json"
    output_dir: str = "artifacts"
    vector_db_dir: str = "vector_db"
    faiss_index_name: str = "faiss.index"
    faiss_meta_name: str = "faiss_metadata.json"
    bm25_index_name: str = "bm25.pkl"
    metadata_name: str = "metadata.db"
    embedding_model: str = "all-MiniLM-L6-v2"
    gcp_endpoint: str = "https://storage.googleapis.com"
    aws_endpoint: str = "https://s3.ap-northeast-2.amazonaws.com"
    deterministic_uuid: bool = True

class IngestionManager:
    def __init__(self, cfg: IngestionConfig):
        self.cfg = cfg
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        self.vector_store = VectorStore(self.cfg.embedding_model)
        self.bm25_store = BM25Store()

    def ingest(self) -> Dict[str, str]:
        storage_map = self._load_json(self.cfg.storage_map_path)
        hotpot_data = self._load_json(self.cfg.hotpot_file_path)

        print(f"Loading data from HotpotQA file: {self.cfg.hotpot_file_path}")
        records = self._collect_records_from_hotpot(hotpot_data, storage_map)

        print("Building FAISS index from records...")
        self.vector_store.build_from_records(records)

        os.makedirs(self.cfg.vector_db_dir, exist_ok=True)
        index_path = os.path.join(self.cfg.vector_db_dir, self.cfg.faiss_index_name)
        vecmeta_path = os.path.join(self.cfg.vector_db_dir, self.cfg.faiss_meta_name)
        self.vector_store.save(index_path, vecmeta_path)

        print("Building BM25 index from records...")
        self.bm25_store.build_from_records(records)
        bm25_path = os.path.join(self.cfg.vector_db_dir, self.cfg.bm25_index_name)
        self.bm25_store.save(bm25_path)

        # Build SQLite metadata.db directly (NO JSON DUMP)
        access_meta = [meta for _, _, meta in records]
        db_path = os.path.join(self.cfg.output_dir, self.cfg.metadata_name)
        if not db_path.endswith(".db") and not db_path.endswith(".sqlite"):
            db_path = os.path.splitext(db_path)[0] + ".db"

        if os.path.exists(db_path):
            os.remove(db_path)

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE metadata (
            global_uuid TEXT PRIMARY KEY,
            file_name TEXT NOT NULL,
            provider TEXT NOT NULL,
            bucket TEXT NOT NULL,
            region TEXT,
            endpoint TEXT,
            raw_json TEXT NOT NULL
        );
        """)
        cur.execute("CREATE INDEX idx_file_name ON metadata(file_name);")
        cur.execute("CREATE INDEX idx_bucket ON metadata(bucket);")

        for r in access_meta:
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

        conn.commit()
        conn.close()
        print(f"Saved access metadata directly to SQLite DB: {db_path}")
        print("\nDual ingestion (FAISS + BM25) and SQLite Metadata complete.")

        return {
            "faiss_index": index_path,
            "faiss_metadata": vecmeta_path,
            "bm25_index": bm25_path,
            "metadata": db_path,
        }

    def _load_json(self, path: str) -> Any:
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _collect_records_from_hotpot(
        self,
        hotpot_data: List[Dict[str, Any]],
        storage_map: Dict[str, Any]
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        doc_map = {}
        print("Extracting unique documents from HotpotQA data...")
        for item in hotpot_data:
            for title, _ in item["supporting_facts"]:
                if title not in doc_map:
                    for ctx_title, sents in item["context"]:
                        if ctx_title == title:
                            doc_map[title] = " ".join(sents)
                            break
        
        print(f"Total unique docs to ingest: {len(doc_map)}")
        
        records: List[Tuple[str, str, Dict[str, Any]]] = []
        for title, content in tqdm(doc_map.items(), desc="Processing documents"):
            file_name = sanitize_filename(title) + ".txt"
            
            storage_info = storage_map.get(file_name, {})
            provider = storage_info.get("provider", "")
            bucket = storage_info.get("bucket", "")
            
            global_uuid = storage_info.get("global_uuid")
            if not global_uuid:
                global_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, file_name))

            endpoint = self.cfg.gcp_endpoint if provider == "gcp" else self.cfg.aws_endpoint

            meta = {
                "file_name": file_name,
                "global_uuid": global_uuid,
                "provider": provider,
                "endpoint": endpoint,
                "parameters": {
                    "iam": storage_info.get("iam", ""),
                    "region": storage_info.get("region", ""),
                    "bucket": bucket,
                },
            }
            records.append((file_name, content, meta))

        if not records:
            raise RuntimeError("No records were collected. Check the HotpotQA data and storage map.")
        return records
