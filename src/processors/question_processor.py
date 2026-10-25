from typing import Dict, Any, List, Optional
import os, json, random, time
from tqdm import tqdm
from langchain_core.prompts import PromptTemplate
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from src.retrievers.permission_retriever import PermissionRetriever, PermissionRetrieverConfig
from concurrent.futures import ThreadPoolExecutor, as_completed

class QuestionProcessor:
    def __init__(self, question_file: str, metadata_file: str, vector_store, user_manager, permission_retriever: Optional[PermissionRetriever] = None):
        self.vector_db_mgr = vector_store
        self.user_manager = user_manager

        with open(question_file, "r", encoding="utf-8") as f:
            self.questions: List[Dict[str, Any]] = json.load(f)
        if metadata_file.endswith(".db") or metadata_file.endswith(".sqlite"):
            import sqlite3
            conn = sqlite3.connect(metadata_file)
            cur = conn.cursor()
            cur.execute("SELECT raw_json FROM metadata")
            metadata: List[Dict[str, Any]] = [json.loads(r[0]) for r in cur.fetchall()]
            conn.close()
        else:
            with open(metadata_file, "r", encoding="utf-8") as f:
                metadata: List[Dict[str, Any]] = json.load(f)

        self.uuid_to_metadata = {m["global_uuid"]: m for m in metadata}
        self.sampled_questions: Optional[List[Dict[str, Any]]] = None

        if os.environ.get("OPENAI_API_KEY"):
            from langchain_openai import ChatOpenAI
            self.llm = ChatOpenAI(temperature=0, model_name="gpt-4o-mini")
        else:
            from src.processors.model_fallback import GeminiFallbackChatModel
            self.llm = GeminiFallbackChatModel()
        self.prompt = PromptTemplate.from_template(
            "You are a strictly grounded AI security assistant.\n"
            "CRITICAL GROUNDING RULES:\n"
            "1. You MUST answer the question EXCLUSIVELY using the explicit facts written in the # Context below.\n"
            "2. You are STRICTLY FORBIDDEN from using any external knowledge, prior pre-training memory, or assumptions.\n"
            "3. If the # Context is empty, or if it does not contain the information needed to answer the question, you MUST respond ONLY: \"I don't know.\"\n"
            "4. Be direct and accurate. State the answer clearly using only facts from the context.\n\n"
            "# Question:\n{question}\n\n# Context:\n{context}\n\n# Answer:"
        )
        self.qa_chain = self.prompt | self.llm | StrOutputParser()
        
        if permission_retriever is None:
            class _DictMetaStore:
                def __init__(self, m): self._m = m
                def get_by_uuid(self, u): return self._m.get(u)
            self.pr = PermissionRetriever(_DictMetaStore(self.uuid_to_metadata), PermissionRetrieverConfig())
        else:
            self.pr = permission_retriever
        
        self.llm_cache: Dict[str, str] = {}

    def sample_questions(self, sample_size: int, seed: Optional[int] = None):
        if seed is not None:
            random.seed(seed)
        if sample_size and sample_size < len(self.questions):
            self.sampled_questions = random.sample(self.questions, sample_size)
        else:
            self.sampled_questions = self.questions

    def run_for_quantitative(
        self,
        ac_manager,
        user_name: str,
        top_k: int = 5,
        max_workers: int = 10,
        question_concurrency: int = 4, # You can adjust the default here
        use_permission_cache: bool = False,
    ) -> Dict[str, Any]:
        
        self.pr.config.max_workers = max_workers
        self.pr.config.use_cache = use_permission_cache

        # --- Ground truth loading (no changes) ---
        def load_expected_map(user: str) -> Dict[str, bool]:
            truth = self.user_manager.get_user_accessible_files(user) or {}
            if isinstance(truth, dict) and truth.get("full_access"):
                return {"__FULL_ACCESS__": True}
            if isinstance(truth, dict) and "files" in truth:
                return {fn: True for fn in truth.get("files", [])}
            if isinstance(truth, dict):
                return {k: bool(v) for k, v in truth.items()}
            if isinstance(truth, list):
                return {fn: True for fn in truth}
            return {}

        def expect_allow(exp_map: Dict[str, bool], file_name: str) -> bool:
            if exp_map.get("__FULL_ACCESS__"):
                return True
            return bool(exp_map.get(file_name, False))

        exp_user = load_expected_map(user_name)
        self.vector_db_mgr.search("warm-up", top_k=1)

        def _process_one_question(q: Dict[str, Any]) -> Dict[str, Any]:
            qtext = q.get("question")
            retrieved = self.vector_db_mgr.search(qtext, top_k=top_k)
            filtered, decisions, _, _ = self.pr.filter_docs(retrieved, ac_manager)
            
            q_TP, q_FP, q_FN, q_TN = 0, 0, 0, 0
            q_expected_true = 0
            uuid_to_row = {r["global_uuid"]: r for r in retrieved}

            for row in retrieved:
                fname = self.uuid_to_metadata.get(row["global_uuid"], {}).get("file_name", "")
                exp = expect_allow(exp_user, fname)
                act = bool(decisions.get(row["global_uuid"], False))
                if exp:
                    q_expected_true += 1
                if act and exp: q_TP += 1
                elif act and not exp: q_FP += 1
                elif (not act) and exp: q_FN += 1
                else: q_TN += 1

            context_contents = []
            for f in filtered:
                src = f if "content" in f else uuid_to_row.get(f.get("global_uuid"), {})
                content = src.get("content", "")
                if content:
                    context_contents.append(content)


            predicted_answer = "ERROR" # Default to ERROR

            for attempt in range(3):
                try:
                    context_string = "\n\n".join(context_contents)
                    
                    out = self.qa_chain.invoke({
                        "context": context_string,
                        "question": qtext
                    })

                    predicted_answer = (out or "").strip() or "I don't know."
                    break 
                except Exception as e:
                    print(f"LLM call failed on attempt {attempt + 1} for Q: '{qtext[:30]}...'. Error: {e}")
                    if attempt < 2:
                        time.sleep(10)
            

            if predicted_answer == "ERROR":
                predicted_answer = "I don't know."

            retrieved_files = [self.uuid_to_metadata.get(row["global_uuid"], {}).get("file_name", "") for row in retrieved]
            accessed_files = [self.uuid_to_metadata.get(f.get("global_uuid"), {}).get("file_name", "") for f in filtered]
            denied_files = [fn for fn in retrieved_files if fn not in accessed_files]

            raw_sup = q.get("supporting_facts", [])
            sup_files = []
            for sf in raw_sup:
                val = sf[0] if isinstance(sf, (list, tuple)) and len(sf) > 0 else sf
                sval = str(val)
                if not sval.endswith(".txt"):
                    sval = f"{sval}.txt"
                sup_files.append(sval)

            return {
                "question_id": q.get("id"),
                "question": qtext,
                "gold_answer": q.get("gold_answer", ""),
                "supporting_docs": sup_files,
                "retrieved_docs": retrieved_files,
                "accessed_docs": accessed_files,
                "denied_docs": denied_files,
                "predicted_answer": predicted_answer,
                "retrieved_count": len(retrieved),
                "q_expected_true": q_expected_true,
                "q_TP": q_TP, "q_FP": q_FP, "q_FN": q_FN, "q_TN": q_TN
            }

        by_question = []
        questions_to_run = self.sampled_questions or self.questions
        
        with ThreadPoolExecutor(max_workers=question_concurrency) as executor:
            future_to_q = {executor.submit(_process_one_question, q): q for q in questions_to_run}
            for future in tqdm(as_completed(future_to_q), 
                               total=len(questions_to_run), 
                               desc=f"Processing questions for {user_name}"):
                try:
                    by_question.append(future.result())
                except Exception as e:
                    q_text = future_to_q[future].get("question", "Unknown Question")
                    print(f"ERROR processing question '{q_text[:50]}...': {e}")

        total_retrieved = sum(r['retrieved_count'] for r in by_question)
        total_expected_authorized = sum(r['q_expected_true'] for r in by_question)
        TP = sum(r['q_TP'] for r in by_question)
        FP = sum(r['q_FP'] for r in by_question)
        FN = sum(r['q_FN'] for r in by_question)
        TN = sum(r['q_TN'] for r in by_question)
        
        permcov = (total_expected_authorized / total_retrieved * 100.0) if total_retrieved else 0.0
        access_match = "PASS" if (FP + FN) == 0 else "FAIL"

        aggregate = {
            "PermissionCoverage_pct": round(permcov, 2),
            "AccessMatch": access_match,
            "TP": TP, "FP": FP, "FN": FN, "TN": TN,
            "total_retrieved": total_retrieved,
            "total_authorized": total_expected_authorized,
        }
        return {"user": user_name, "aggregate": aggregate, "by_question": by_question}
    
    def run_for_latency(self, ac_manager, top_k=5, max_workers=10):
        self.pr.config.max_workers = max_workers

        results = []
        user_name = ac_manager.user_name
        questions_to_run = self.sampled_questions or self.questions

        for q in tqdm(questions_to_run, desc=f"Latency run for {user_name}"):
            q_id = q["id"]
            q_text = q["question"]
            q_gold_answer = q.get("gold_answer")

            retrieved = self.vector_db_mgr.search(q_text, top_k=top_k)

            filtered, decisions, per_uuid_durs, perm_time = self.pr.filter_docs(retrieved, ac_manager)
            context_contents = [d["content"] for d in filtered if d.get("content")]

            predicted_answer = "ERROR"
            for attempt in range(3):
                try:
                    context_string = "\n\n".join(context_contents)
                    predicted_answer = self.qa_chain.invoke({
                        "context": context_string, 
                        "question": q_text
                    })
                    break
                except Exception as e:
                    print(f"LLM call failed on attempt {attempt + 1} for Q: '{q_text[:30]}...'. Error: {e}")
                    if attempt < 2:
                        time.sleep(10)

            doc_results = [{"uuid": d["global_uuid"], "metadata": self.uuid_to_metadata.get(d["global_uuid"], {})}
                           for d in retrieved]

            results.append({
                "question_id": q_id,
                "question": q_text,
                "gold_answer": q_gold_answer,
                "predicted_answer": predicted_answer,
                "context_docs": context_contents,
                "total_access_check_duration": perm_time,
                "uuid_access_check_durations": per_uuid_durs,
                "doc_results": doc_results,
                "retrieved_count": len(retrieved),
                "allowed_count": len(filtered),
            })

        return {"user": user_name, "questions": results}
