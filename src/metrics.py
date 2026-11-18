"""Metrics computation module: computes all localization, gate, and cost statistics strictly from disk logs."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("evidrover.metrics")


def load_pipeline_traces(runs_dir: Path, pipeline_name: str) -> List[Dict[str, Any]]:
    """Loads all JSONL trace records for a pipeline from runs/<pipeline>/."""
    pipeline_dir = runs_dir / pipeline_name
    if not pipeline_dir.exists():
        return []

    traces = []
    for f in sorted(pipeline_dir.glob("*.jsonl")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                line = fh.readline().strip()
                if line:
                    data = json.loads(line)
                    if "metrics" in data:
                        traces.append(data)
        except Exception as e:
            logger.warning(f"Failed to read trace file {f}: {e}")
    return traces


def compute_pipeline_metrics(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes summary statistics across a set of instance traces."""
    if not traces:
        return {"status": "not_run", "instances_count": 0}

    n = len(traces)
    file_acc1 = [t["metrics"]["file_acc1"] for t in traces]
    file_acc3 = [t["metrics"]["file_acc3"] for t in traces]
    file_acc5 = [t["metrics"]["file_acc5"] for t in traces]
    file_recall5 = [t["metrics"]["file_recall5"] for t in traces]
    func_acc5 = [t["metrics"]["func_acc5"] for t in traces]
    gold_in_ev = [int(t["metrics"].get("gold_in_evidence", False)) for t in traces]

    tokens = [t["metrics"]["tokens_used"] for t in traces]
    costs = [t["metrics"]["cost_dollars"] for t in traces]
    calls = [t["metrics"]["llm_calls"] for t in traces]
    rounds = [t["total_rounds"] for t in traces]
    wall_times = [t["metrics"]["wall_time_sec"] for t in traces]

    # Citations analysis
    cited_counts_by_source: Dict[str, int] = defaultdict(int)
    total_ev_by_source: Dict[str, int] = defaultdict(int)
    for t in traces:
        cited_ids = set(t.get("final_ranking", {}).get("evidence_ids_cited", []))
        for it in t.get("evidence_items", []):
            src = it.get("source", "unknown")
            total_ev_by_source[src] += 1
            if it.get("id") in cited_ids:
                cited_counts_by_source[src] += 1

    citation_shares = {}
    for src, tot in total_ev_by_source.items():
        citation_shares[src] = round(cited_counts_by_source[src] / max(1, tot), 3)

    return {
        "status": "completed",
        "instances_count": n,
        "file_acc1": round(float(np.mean(file_acc1)), 4),
        "file_acc3": round(float(np.mean(file_acc3)), 4),
        "file_acc5": round(float(np.mean(file_acc5)), 4),
        "file_recall5": round(float(np.mean(file_recall5)), 4),
        "func_acc5": round(float(np.mean(func_acc5)), 4),
        "gold_in_evidence_rate": round(float(np.mean(gold_in_ev)), 4),
        "tokens": {
            "mean": round(float(np.mean(tokens)), 1),
            "median": round(float(np.median(tokens)), 1),
            "total": int(np.sum(tokens)),
        },
        "cost_dollars": {
            "mean": round(float(np.mean(costs)), 4),
            "median": round(float(np.median(costs)), 4),
            "total": round(float(np.sum(costs)), 4),
        },
        "llm_calls": {
            "mean": round(float(np.mean(calls)), 1),
            "median": round(float(np.median(calls)), 1),
        },
        "rounds": {
            "mean": round(float(np.mean(rounds)), 2),
            "median": round(float(np.median(rounds)), 2),
        },
        "wall_time_sec": {
            "mean": round(float(np.mean(wall_times)), 2),
            "median": round(float(np.median(wall_times)), 2),
        },
        "citation_shares": citation_shares,
    }


def compute_gate_quality(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes gate quality metrics for Pipeline F:
    Confusion matrix of gate 'sufficient' vs whether gold was in retrieved evidence.
    """
    if not traces:
        return {}

    tp = 0  # Said insufficient and gold was missing (Correct flag)
    fp = 0  # Said insufficient but gold was present
    fn = 0  # Said sufficient but gold was missing (Dangerous failure)
    tn = 0  # Said sufficient and gold was present

    by_round: Dict[int, Dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})

    for t in traces:
        rounds = t.get("rounds", [])
        gold_files = set(t.get("gold_files", []))
        
        cumulative_locators: Set[str] = set()
        for r_idx, r in enumerate(rounds, start=1):
            verdict = r.get("gate_verdict")
            if not verdict:
                continue

            # Check if gold file was in evidence up to this round
            # We look at the evidence retrieved in this round
            # In trace, evidence items are tracked
            has_gold = t["metrics"].get("gold_in_evidence", False)
            sufficient = verdict.get("passed", False) or verdict.get("sufficient", False)

            if not sufficient and not has_gold:
                tp += 1
            elif not sufficient and has_gold:
                fp += 1
            elif sufficient and not has_gold:
                fn += 1
            elif sufficient and has_gold:
                tn += 1

            # Round accuracy
            is_correct = (sufficient == has_gold)
            by_round[r_idx]["total"] += 1
            if is_correct:
                by_round[r_idx]["correct"] += 1

    precision = tp / max(1, (tp + fp))
    recall = tp / max(1, (tp + fn))

    round_accs = {}
    for r_idx, counts in sorted(by_round.items()):
        round_accs[f"round_{r_idx}"] = round(counts["correct"] / max(1, counts["total"]), 3)

    return {
        "confusion_matrix": {"TP": tp, "FP": fp, "FN": fn, "TN": tn},
        "precision_flagging_missing_gold": round(precision, 4),
        "recall_flagging_missing_gold": round(recall, 4),
        "gate_accuracy_by_round": round_accs,
    }


def compute_escalation_statistics(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes escalation behavior statistics:
    source usage frequencies, escalation rate, gap types, and helped counts.
    """
    if not traces:
        return {}

    total_instances = len(traces)
    escalated_instances = 0
    source_usage: Dict[str, int] = defaultdict(int)
    gap_types: Dict[str, int] = defaultdict(int)
    source_helped: Dict[str, int] = defaultdict(int)

    for t in traces:
        rounds = t.get("rounds", [])
        if len(rounds) > 1:
            escalated_instances += 1

        gold_files = set(t.get("gold_files", []))
        gold_found = False

        for r in rounds:
            src = r.get("source", "unknown")
            source_usage[src] += 1

            verdict = r.get("gate_verdict")
            if verdict:
                gap = verdict.get("gap_type", "none")
                gap_types[gap] += 1

            # Check if this source introduced gold file for the first time
            if not gold_found:
                # Check items in trace
                for it in t.get("evidence_items", []):
                    if it.get("retrieved_by_round") == r.get("round_num") and any(gf in it.get("locator", "") for gf in gold_files):
                        source_helped[src] += 1
                        gold_found = True
                        break

    escalation_rate = round(escalated_instances / max(1, total_instances), 4)

    return {
        "escalation_rate": escalation_rate,
        "source_usage_counts": dict(source_usage),
        "gap_types_distribution": dict(gap_types),
        "source_helped_first_gold_appearance": dict(source_helped),
    }


def compute_paired_bootstrap(
    traces_a: List[Dict[str, Any]],
    traces_b: List[Dict[str, Any]],
    n_resamples: int = 1000,
    seed: int = 42,
) -> Dict[str, Any]:
    """Computes win/tie/loss and paired 95% bootstrap confidence interval for Acc@5 difference."""
    map_a = {t["instance_id"]: t["metrics"]["file_acc5"] for t in traces_a}
    map_b = {t["instance_id"]: t["metrics"]["file_acc5"] for t in traces_b}

    common_ids = sorted(set(map_a.keys()).intersection(set(map_b.keys())))
    if not common_ids:
        return {"error": "no_common_instances"}

    scores_a = np.array([map_a[i] for i in common_ids])
    scores_b = np.array([map_b[i] for i in common_ids])

    diffs = scores_a - scores_b
    wins = int(np.sum(diffs > 0))
    losses = int(np.sum(diffs < 0))
    ties = int(np.sum(diffs == 0))

    rng = np.random.RandomState(seed)
    boot_diffs = []
    n = len(diffs)
    for _ in range(n_resamples):
        sample_indices = rng.randint(0, n, size=n)
        boot_diffs.append(np.mean(diffs[sample_indices]))

    ci_lower = float(np.percentile(boot_diffs, 2.5))
    ci_upper = float(np.percentile(boot_diffs, 97.5))
    mean_diff = float(np.mean(diffs))

    return {
        "common_instances": n,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "mean_acc5_diff": round(mean_diff, 4),
        "bootstrap_ci_95": [round(ci_lower, 4), round(ci_upper, 4)],
        "statistically_significant": bool(ci_lower > 0 or ci_upper < 0),
    }


def compute_failure_analysis(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Auto-assigns failure category for failed instances in Pipeline F."""
    failures = []
    for t in traces:
        if t["metrics"]["file_acc5"] == 0:
            gold_in_ev = t["metrics"].get("gold_in_evidence", False)
            rounds = t.get("rounds", [])

            # Determine category
            category = "gold_never_retrieved"
            if gold_in_ev:
                category = "retrieved_but_not_ranked"
            else:
                for r in rounds:
                    v = r.get("gate_verdict")
                    if v and (v.get("passed") or v.get("sufficient")):
                        category = "gate_wrongly_said_sufficient"
                        break

            failures.append({
                "instance_id": t["instance_id"],
                "repo": t["repo"],
                "gold_files": t["gold_files"],
                "total_rounds": t["total_rounds"],
                "auto_category": category,
                "student_manual_label": "",  # To be labeled by the student
            })
    return failures
