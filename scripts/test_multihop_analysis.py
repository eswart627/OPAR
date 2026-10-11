import os
import sys
import json

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from src.stores.vector_store import VectorStore

def main():
    vs = VectorStore("all-MiniLM-L6-v2")
    vs.load("artifacts/faiss.index", "artifacts/faiss_metadata.json")

    dev_data = json.load(open("artifacts/hotpot_dev_distractor_v1.json", "r", encoding="utf-8"))
    dev_map = {x["question"]: x for x in dev_data}

    sampled_questions = [
        "In what year was the university where Sergei Aleksandrovich Tokarev was a professor founded?",
        "Which actor does American Beauty and American Beauty have in common?",
        "Who was hung for assisting the attempted surrender of a defector from the American Continental Army to the British Army?",
        "Which major international airport in south-east England ranks as the 8th busiest airport in Europe and replaced Croydon Airport?"
    ]

    for i, q_text in enumerate(sampled_questions):
        gold_item = dev_map.get(q_text, {})
        gold_sup = gold_item.get("supporting_facts", [])
        gold_ans = gold_item.get("answer", "")
        
        results = vs.search(q_text, top_k=5)
        retr_keys = [r["file_name"] for r in results]
        
        print("=" * 85)
        print(f"QUESTION {i+1}: {q_text}")
        print("=" * 85)
        print(f"GOLD ANSWER:             {gold_ans}")
        print(f"GOLD SUPPORTING DOCS:    {[s[0] for s in gold_sup]}")
        print(f"FAISS RETRIEVED (Top-5): {retr_keys}")
        
        # Check which supporting docs were caught in top 5
        gold_titles = [s[0].lower().replace(" ", "_").replace("-", "_") for s in gold_sup]
        hits = []
        for g in gold_titles:
            for r in retr_keys:
                r_clean = r.lower().replace(".txt", "").replace(" ", "_").replace("-", "_")
                if g in r_clean or r_clean in g:
                    hits.append(r)
                    break
        print(f"SUPPORTING DOCS FOUND:   {hits} (Retrieved {len(hits)} of {len(gold_sup)} required)")
        
        # Print snippet of each supporting doc from dev_data context if available
        print("\nREASONING CHAIN (HOPS):")
        ctx = dict(gold_item.get("context", []))
        for sup in gold_sup:
            title = sup[0]
            sent_idx = sup[1]
            if title in ctx and len(ctx[title]) > sent_idx:
                safe_text = ctx[title][sent_idx].encode("ascii", "replace").decode("ascii")
                print(f"  * Evidence from [{title}]: \"{safe_text}\"")
        print("\n")

if __name__ == "__main__":
    main()
