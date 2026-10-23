import os
from typing import List, Dict, Any, Optional
from src.stores.vector_store import VectorStore
from src.stores.bm25_store import BM25Store


class HybridRetriever:
    """
    Dual-mode retriever supporting:
      1. 'faiss': Pure dense vector retrieval.
      2. 'hybrid': Weighted Reciprocal Rank Fusion (RRF) between FAISS and BM25.
         High BM25 weighting (default 0.75 BM25, 0.25 FAISS) optimized for
         numeric, technical identifiers (PMID, dates, codes, exact entities).
    """

    def __init__(
        self,
        vector_store: VectorStore,
        bm25_store: Optional[BM25Store] = None,
        mode: str = "faiss",
        bm25_weight: float = 0.75,
        faiss_weight: float = 0.25,
        rrf_k: int = 60,
    ):
        self.vector_store = vector_store
        self.bm25_store = bm25_store
        self.mode = mode.lower()
        self.bm25_weight = bm25_weight
        self.faiss_weight = faiss_weight
        self.rrf_k = rrf_k

    def set_mode(self, mode: str):
        if mode.lower() not in ("faiss", "hybrid"):
            raise ValueError(f"Unknown mode: {mode}. Must be 'faiss' or 'hybrid'.")
        self.mode = mode.lower()

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        # If mode is faiss or no BM25 store provided, fall back to pure FAISS
        if self.mode == "faiss" or self.bm25_store is None:
            return self.vector_store.search(query, top_k=top_k)

        # Retrieve candidates from both systems (pull top_k * 2 to give fusion pool)
        fetch_k = max(top_k * 3, 15)
        dense_results = self.vector_store.search(query, top_k=fetch_k)
        sparse_results = self.bm25_store.search(query, top_k=fetch_k)

        # Weighted Reciprocal Rank Fusion (RRF)
        # RRF(d) = w_dense / (k + rank_dense) + w_sparse / (k + rank_sparse)
        doc_scores: Dict[str, float] = {}
        doc_entries: Dict[str, Dict[str, Any]] = {}

        # 1. Process Dense Ranks
        for rank, row in enumerate(dense_results, start=1):
            fname = row["file_name"]
            score = self.faiss_weight * (1.0 / (self.rrf_k + rank))
            doc_scores[fname] = doc_scores.get(fname, 0.0) + score
            if fname not in doc_entries:
                doc_entries[fname] = row.copy()

        # 2. Process BM25 Ranks
        for rank, row in enumerate(sparse_results, start=1):
            fname = row["file_name"]
            score = self.bm25_weight * (1.0 / (self.rrf_k + rank))
            doc_scores[fname] = doc_scores.get(fname, 0.0) + score
            if fname not in doc_entries:
                doc_entries[fname] = row.copy()

        # Sort descending by fused RRF score
        sorted_fnames = sorted(doc_scores.keys(), key=lambda fn: doc_scores[fn], reverse=True)

        final_results = []
        for fname in sorted_fnames[:top_k]:
            entry = doc_entries[fname]
            entry["rrf_score"] = doc_scores[fname]
            final_results.append(entry)

        return final_results
