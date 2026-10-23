import os
import re
import pickle
from typing import List, Dict, Any, Tuple
from rank_bm25 import BM25Okapi


def bm25_tokenizer(text: str) -> List[str]:
    """
    Tokenizes text preserving numbers, IDs, and alphanumeric tokens.
    Lowercases but keeps punctuation-free tokens, e.g., 'PMID: 42596018' -> ['pmid', '42596018'].
    """
    tokens = re.findall(r"\b\w+\b", text.lower())
    return tokens


class BM25Store:
    def __init__(self):
        self.bm25: BM25Okapi = None
        self.doc_meta: List[Dict[str, Any]] = []

    def build_from_records(self, records: List[Tuple[str, str, Dict[str, Any]]]) -> None:
        """
        Builds BM25 index from records.
        records is list of (file_name, content, meta_dict).
        """
        tokenized_corpus = []
        self.doc_meta = []

        for (_, content, meta) in records:
            tokens = bm25_tokenizer(content)
            tokenized_corpus.append(tokens)
            self.doc_meta.append({
                "file_name": meta["file_name"],
                "global_uuid": meta["global_uuid"],
                "content": content,
            })

        self.bm25 = BM25Okapi(tokenized_corpus)

    def save(self, index_path: str) -> None:
        if self.bm25 is None:
            raise RuntimeError("BM25 index not built.")
        with open(index_path, "wb") as f:
            pickle.dump({"bm25": self.bm25, "doc_meta": self.doc_meta}, f)
        print(f"Saved BM25 index to {index_path}")

    def load(self, index_path: str) -> None:
        if not os.path.exists(index_path):
            raise FileNotFoundError(f"BM25 index file not found at {index_path}")
        with open(index_path, "rb") as f:
            data = pickle.load(f)
            self.bm25 = data["bm25"]
            self.doc_meta = data["doc_meta"]

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        if self.bm25 is None:
            raise RuntimeError("BM25 index not loaded or built.")

        query_tokens = bm25_tokenizer(query)
        scores = self.bm25.get_scores(query_tokens)

        # Sort indices descending by score
        ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

        results = []
        for idx in ranked_indices:
            row = self.doc_meta[idx].copy()
            row["bm25_score"] = float(scores[idx])
            results.append(row)
        return results
