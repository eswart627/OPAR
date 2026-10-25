# web/services/evaluator_service.py
# Metrics evaluation service for Exact Match and Token-Level F1

from typing import Dict, Any
from scripts.helpers.hotpot_evaluate_v1 import exact_match_score, f1_score

class EvaluatorService:
    @staticmethod
    def evaluate(prediction: str, ground_truth: str) -> Dict[str, float]:
        """Calculates exact match and F1 overlap between prediction and gold answer."""
        if not ground_truth:
            return {"exact_match": 0.0, "f1": 0.0}
        
        em = 1.0 if exact_match_score(prediction, ground_truth) else 0.0
        f1, prec, recall = f1_score(prediction, ground_truth)
        return {
            "exact_match": float(em),
            "f1": round(float(f1), 4),
            "precision": round(float(prec), 4),
            "recall": round(float(recall), 4)
        }
