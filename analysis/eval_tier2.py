"""Evaluation script for Tier 2 Verifier vs Human Correctness Labels.

Computes TP, TN, FP, FN, precision, recall, and Cohen's Kappa between
the evidence verifier's decisions and human ground-truth labels.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

logger = logging.getLogger("evidrover.eval_tier2")


def compute_cohen_kappa(matrix: np.ndarray) -> float:
    """Compute Cohen's Kappa coefficient for inter-rater agreement."""
    n = np.sum(matrix)
    if n == 0:
        return 0.0

    po = (matrix[0, 0] + matrix[1, 1]) / n
    row_sums = np.sum(matrix, axis=1)
    col_sums = np.sum(matrix, axis=0)
    pe = (row_sums[0] * col_sums[0] + row_sums[1] * col_sums[1]) / (n * n)

    if pe == 1.0:
        return 1.0
    return float((po - pe) / (1.0 - pe))


def evaluate_tier2(
    human_labels_path: str = "data/human_labels.csv",
    tier2_runs_dir: str = "runs/tier2",
) -> Dict[str, Any]:
    csv_file = Path(human_labels_path)
    if not csv_file.exists():
        print(f"Error: Human labels file not found at {csv_file}")
        return {}

    # Read human labels
    human_data: Dict[str, str] = {}
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            iid = row.get("instance_id", "").strip()
            label = row.get("student_label", "").strip().lower()
            if label in {"correct", "incorrect", "true", "false", "1", "0"}:
                human_data[iid] = "correct" if label in {"correct", "true", "1"} else "incorrect"

    if not human_data:
        print("Note: No student labels found in data/human_labels.csv yet.")
        print("To evaluate Tier 2 verifier agreement:")
        print("1. Open data/human_labels.csv")
        print("2. Set 'student_label' to 'correct' or 'incorrect' for instances.")
        print("3. Re-run: python analysis/eval_tier2.py")
        return {"status": "labels_pending"}

    # Read verifier reports
    runs_dir = Path(tier2_runs_dir)
    verifier_decisions: Dict[str, str] = {}
    if runs_dir.exists():
        for f in runs_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                iid = data.get("instance_id")
                verdict = data.get("verdict", "").lower()  # 'accepted' or 'rejected'
                if iid and verdict:
                    verifier_decisions[iid] = "correct" if verdict == "accepted" else "incorrect"
            except Exception:
                pass

    common_ids = [iid for iid in human_data if iid in verifier_decisions]
    if not common_ids:
        print(f"Found {len(human_data)} human labels, but no matching verifier results in {runs_dir}.")
        print("Run Tier 2 patch verification first to generate verifier predictions.")
        return {"status": "no_matching_runs"}

    tp = 0  # Both say correct
    tn = 0  # Both say incorrect
    fp = 0  # Verifier says correct, Human says incorrect
    fn = 0  # Verifier says incorrect, Human says correct

    for iid in common_ids:
        h = human_data[iid]
        v = verifier_decisions[iid]
        if v == "correct" and h == "correct":
            tp += 1
        elif v == "incorrect" and h == "incorrect":
            tn += 1
        elif v == "correct" and h == "incorrect":
            fp += 1
        elif v == "incorrect" and h == "correct":
            fn += 1

    matrix = np.array([[tp, fp], [fn, tn]])
    kappa = compute_cohen_kappa(matrix)
    precision = tp / max(1, (tp + fp))
    recall = tp / max(1, (tp + fn))
    accuracy = (tp + tn) / max(1, len(common_ids))

    results = {
        "status": "completed",
        "evaluated_instances": len(common_ids),
        "contingency_matrix": {"TP": tp, "FP": fp, "FN": fn, "TN": tn},
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "cohen_kappa": round(kappa, 4),
    }

    print("\n=== Tier 2 Verifier vs Human Agreement ===")
    print(f"Evaluated Instances: {len(common_ids)}")
    print(f"TP (Accepted & Correct):    {tp}")
    print(f"TN (Rejected & Incorrect):  {tn}")
    print(f"FP (Accepted & Incorrect):  {fp}")
    print(f"FN (Rejected & Correct):    {fn}")
    print(f"Agreement Accuracy:         {accuracy:.2%}")
    print(f"Precision:                  {precision:.4f}")
    print(f"Recall:                     {recall:.4f}")
    print(f"Cohen's Kappa:              {kappa:.4f}")
    return results


if __name__ == "__main__":
    evaluate_tier2()
