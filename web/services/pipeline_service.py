# web/services/pipeline_service.py
# Singleton lifecycle and dependency loader for Permission-Aware RAG components

import os
import sys
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("RAG_Web_PipelineService")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.env_loader import load_project_env
load_project_env()

from src.managers.credential_manager import CredentialManager
from src.stores.vector_store import VectorStore
from src.stores.bm25_store import BM25Store
from src.stores.metadata_store import MetadataStore
from src.retrievers.hybrid_retriever import HybridRetriever
from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig
from src.processors.question_processor import QuestionProcessor

class PipelineService:
    _instance: Optional["PipelineService"] = None

    def __init__(self):
        self.artifacts_dir = os.path.join(BASE_DIR, "artifacts")
        self.vector_db_dir = os.path.join(BASE_DIR, "vector_db")
        self.results_dir = os.path.join(BASE_DIR, "results")
        
        self.user_manager = None
        self.vector_store = None
        self.bm25_store = None
        self.metadata_store = None
        self.permission_retriever = None
        self.question_processor = None
        self.active_retrievers: Dict[str, HybridRetriever] = {}
        self.is_ready = False
        
        self.reload()

    @classmethod
    def get_instance(cls) -> "PipelineService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reload(self) -> bool:
        """Reloads indices and components dynamically after ingestion."""
        try:
            cred_file = os.path.join(self.artifacts_dir, "test_credential.txt")
            if os.path.exists(cred_file):
                self.user_manager = CredentialManager(base_path=self.artifacts_dir, file_name="test_credential.txt")

            faiss_index = os.path.join(self.vector_db_dir, "faiss.index")
            faiss_meta = os.path.join(self.vector_db_dir, "faiss_metadata.json")
            if os.path.exists(faiss_index) and os.path.exists(faiss_meta):
                self.vector_store = VectorStore(model_name="all-MiniLM-L6-v2")
                self.vector_store.load(faiss_index, faiss_meta)

            bm25_index = os.path.join(self.vector_db_dir, "bm25.pkl")
            if os.path.exists(bm25_index):
                self.bm25_store = BM25Store()
                self.bm25_store.load(bm25_index)

            meta_file = os.path.join(self.artifacts_dir, "metadata.json")
            if os.path.exists(meta_file):
                self.metadata_store = MetadataStore()
                self.metadata_store.load(meta_file)

            if self.metadata_store:
                self.permission_retriever = PermissionRetriever(
                    self.metadata_store,
                    PermissionRetrieverConfig(max_workers=1, use_cache=True)
                )

            # Build mode retrievers
            if self.vector_store:
                self.active_retrievers["faiss"] = HybridRetriever(
                    vector_store=self.vector_store,
                    bm25_store=self.bm25_store,
                    mode="faiss",
                )
            if self.vector_store and self.bm25_store:
                self.active_retrievers["hybrid"] = HybridRetriever(
                    vector_store=self.vector_store,
                    bm25_store=self.bm25_store,
                    mode="hybrid",
                    bm25_weight=0.75,
                    faiss_weight=0.25,
                    rrf_k=60,
                )

            q_file = os.path.join(self.artifacts_dir, "question_list.json")
            if not os.path.exists(q_file):
                q_file = os.path.join(self.artifacts_dir, "hotpot_dev_distractor_v1.json")

            if (self.user_manager and self.metadata_store and self.active_retrievers and 
                os.path.exists(q_file) and os.path.exists(meta_file)):
                default_retriever = self.active_retrievers.get("hybrid") or self.active_retrievers.get("faiss")
                self.question_processor = QuestionProcessor(
                    q_file,
                    meta_file,
                    default_retriever,
                    self.user_manager,
                    permission_retriever=self.permission_retriever
                )
                self.is_ready = True
                logger.info("[INFO] PipelineService components successfully initialized.")
                return True
        except Exception as e:
            logger.warning(f"[WARN] Partial pipeline initialization: {e}")
            self.is_ready = False
            return False
