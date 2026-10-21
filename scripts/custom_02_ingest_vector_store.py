"""
scripts/custom_02_ingest_vector_store.py

Step 2: Vector Store Ingestion (768 Dimensions - BAAI/bge-base-en-v1.5).
Ingests our 530 corporate articles into isolated custom artifacts:
  - Supports --store-type hybrid (FAISS 768-dim + BM25)
  - Supports --store-type faiss  (Dense FAISS 768-dim)

Outputs:
  - artifacts/custom_artifacts/faiss_<store_type>.index
  - artifacts/custom_artifacts/metadata.json
  - artifacts/custom_artifacts/bm25.pkl (for hybrid)
"""

import os
import sys
import json
import uuid
import pickle
import argparse
from pathlib import Path
from typing import List, Dict, Any, Tuple
from collections import defaultdict
import numpy as np
import faiss
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class HybridStore:
    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5"):
        self.model_name = model_name
        self.model = SentenceTransformer(self.model_name)
        self.faiss_index = None
        self.bm25 = None
        self.vector_meta = []

    def build_and_save(self, records: List[Tuple[str, str, Dict[str, Any]]], index_path: str, meta_path: str, bm25_path: str):
        texts = [content for _, content, _ in records]
        print(f"[HybridStore] Encoding {len(texts)} documents with {self.model_name} (768 dims)...")
        embeddings = self.model.encode(texts, batch_size=32, show_progress_bar=True, convert_to_numpy=True)
        embeddings = np.asarray(embeddings, dtype=np.float32)

        # 1. Dense IndexFlatL2
        self.faiss_index = faiss.IndexFlatL2(embeddings.shape[1])
        self.faiss_index.add(embeddings)
        faiss.write_index(self.faiss_index, index_path)

        # 2. Sparse BM25
        print("[HybridStore] Building BM25 sparse index...")
        tokenized_corpus = [[w.lower() for w in text.split()] for text in texts]
        self.bm25 = BM25Okapi(tokenized_corpus)
        with open(bm25_path, "wb") as f:
            pickle.dump(self.bm25, f)

        # 3. Metadata
        self.vector_meta = []
        for (_, content, meta) in records:
            self.vector_meta.append({
                "file_name": meta["file_name"],
                "global_uuid": meta["global_uuid"],
                "content": content,
            })
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(self.vector_meta, f, indent=2, ensure_ascii=False)


class DenseFAISSStore:
    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5"):
        self.model_name = model_name
        self.model = SentenceTransformer(self.model_name)
        self.faiss_index = None
        self.vector_meta = []

    def build_and_save(self, records: List[Tuple[str, str, Dict[str, Any]]], index_path: str, meta_path: str):
        texts = [content for _, content, _ in records]
        print(f"[DenseStore] Encoding {len(texts)} documents with {self.model_name} (768 dims)...")
        embeddings = self.model.encode(texts, batch_size=32, show_progress_bar=True, convert_to_numpy=True)
        embeddings = np.asarray(embeddings, dtype=np.float32)

        self.faiss_index = faiss.IndexFlatL2(embeddings.shape[1])
        self.faiss_index.add(embeddings)
        faiss.write_index(self.faiss_index, index_path)

        self.vector_meta = []
        for (_, content, meta) in records:
            self.vector_meta.append({
                "file_name": meta["file_name"],
                "global_uuid": meta["global_uuid"],
                "content": content,
            })
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(self.vector_meta, f, indent=2, ensure_ascii=False)


def run_ingestion(dataset_dir: str, store_type: str = "hybrid"):
    print("=" * 80)
    print(f"STEP 02: INGESTING VECTOR STORE ({store_type.upper()} - 768 DIMS)")
    print("=" * 80)

    dataset_path = Path(dataset_dir)
    storage_map_file = dataset_path / "file_to_storage_info.json"
    raw_articles_dir = dataset_path / "raw_articles"

    if not storage_map_file.exists() or not raw_articles_dir.exists():
        raise FileNotFoundError(f"Export directory missing files at: {dataset_dir}")

    with open(storage_map_file, "r", encoding="utf-8") as f:
        storage_map = json.load(f)

    custom_artifacts = PROJECT_ROOT / "artifacts" / "custom_artifacts"
    custom_artifacts.mkdir(parents=True, exist_ok=True)

    # Collect article records with deterministic UUIDs
    print(f"Loading {len(storage_map)} articles from: {raw_articles_dir}")
    records = []
    access_metadata = []

    for fname, sinfo in storage_map.items():
        fpath = raw_articles_dir / fname
        if not fpath.exists():
            continue
        content = fpath.read_text(encoding="utf-8")

        provider = sinfo.get("provider", "gcp")
        bucket = sinfo.get("bucket", "")
        key = f"{provider}:{bucket}:{fname}"
        global_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, key))

        meta = {
            "file_name": fname,
            "global_uuid": global_uuid,
            "provider": provider,
            "parameters": {
                "bucket": bucket,
                "iam": sinfo.get("iam", ""),
                "region": sinfo.get("region", ""),
            }
        }
        records.append((fname, content, meta))
        access_metadata.append(meta)

    # Save metadata.json for MetadataStore
    meta_path = custom_artifacts / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(access_metadata, f, indent=2, ensure_ascii=False)
    print(f"[SUCCESS] Saved access metadata ({len(access_metadata)} docs) to: {meta_path}")

    # Build and save vector store
    index_path = custom_artifacts / f"faiss_{store_type}.index"
    vecmeta_path = custom_artifacts / f"faiss_{store_type}_metadata.json"
    bm25_path = custom_artifacts / "bm25.pkl"

    if store_type == "hybrid":
        store = HybridStore()
        store.build_and_save(records, str(index_path), str(vecmeta_path), str(bm25_path))
    else:
        store = DenseFAISSStore()
        store.build_and_save(records, str(index_path), str(vecmeta_path))

    print(f"\n[SUCCESS] Vector store built and saved to:")
    print(f"  - FAISS Index: {index_path} (768-dim)")
    if store_type == "hybrid":
        print(f"  - BM25 Pickle: {bm25_path}")
    print(f"  - Metadata:    {vecmeta_path}")
    print("\n" + "=" * 80)
    print("STEP 02 COMPLETE.")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step 02: Ingest Vector Store.")
    parser.add_argument(
        "--dataset-dir",
        type=str,
        default=r"C:\Users\pichi\Desktop\MultiHop_Dataset_Creation_Pipeline\data\permission_aware_export",
        help="Path to exported MultiHop dataset directory."
    )
    parser.add_argument(
        "--store-type",
        choices=["hybrid", "faiss"],
        default="hybrid",
        help="Retrieval store type: 'hybrid' (BM25 + 768-dim FAISS) or 'faiss' (dense only)."
    )
    args = parser.parse_args()
    run_ingestion(args.dataset_dir, store_type=args.store_type)

