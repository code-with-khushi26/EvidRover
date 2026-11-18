"""Pipelines module for bug localization: B, C, E, F, and G configurations."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.analyzer import IssueAnalysis, IssueAnalyzer
from src.data import Instance
from src.escalation import EscalationDecision, EscalationPolicy
from src.evidence import Evidence, EvidenceSet
from src.gate import GateVerdict, SufficiencyGate
from src.indexes.code import CodeIndex
from src.indexes.commit import CommitIndex
from src.indexes.doc import DocIndex
from src.indexes.issue import IssueIndex
from src.llm import LLMClient

logger = logging.getLogger("evidrover.pipelines")


@dataclass
class RankingResult:
    """Ranked localization candidates and cited evidence IDs."""
    files: List[str]
    functions: List[str]
    evidence_ids_cited: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


RANKING_SYSTEM_PROMPT = """You are an expert software localization agent.
Given the issue description and the collected evidence items (with IDs [E1], [E2], ...),
identify up to 5 candidate files and up to 5 candidate functions/methods most likely to require bug fixing.

Return strict JSON:
{
  "files": ["relative/path/to/file1.py", "relative/path/to/file2.py"],
  "functions": ["relative/path/to/file1.py:function_name", "relative/path/to/file2.py:method_name"],
  "evidence_ids_cited": ["E1", "E2"]
}
Only output the JSON object, nothing else.
"""


def rank_candidates(
    llm: LLMClient,
    instance: Instance,
    analysis: Optional[IssueAnalysis],
    evidence_set: EvidenceSet,
) -> RankingResult:
    """Invokes LLM to select and rank top 5 candidate files and functions."""
    ev_prompt, packed_items = evidence_set.pack_for_prompt()

    prompt = (
        f"=== REPOSITORY ===\n{instance.repo}\n\n"
        f"=== ISSUE DESCRIPTION ===\n{instance.problem_statement[:2500]}\n\n"
        f"=== COLLECTED EVIDENCE ===\n{ev_prompt}\n\n"
        f"Produce the top 5 ranked files and functions in strict JSON format."
    )

    res = llm.complete(
        prompt=prompt,
        system=RANKING_SYSTEM_PROMPT,
        json_schema={"type": "object"},
    )

    try:
        cleaned = res.text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        data = json.loads(cleaned.strip())
        files = [str(f) for f in data.get("files", [])][:5]
        functions = [str(fn) for fn in data.get("functions", [])][:5]
        cites = [str(c) for c in data.get("evidence_ids_cited", [])]
        return RankingResult(files=files, functions=functions, evidence_ids_cited=cites)
    except Exception as e:
        logger.warning(f"Failed to parse ranking JSON ({e}); extracting from evidence locators.")
        # Fallback: extract unique file paths and function entities from evidence locators
        files: List[str] = []
        funcs: List[str] = []
        cites: List[str] = []
        for it in evidence_set.items():
            cites.append(it.id)
            if it.source == "code":
                path = it.locator.split(":")[0]
                if path not in files:
                    files.append(path)
                # Check entity header
                match = re.search(r"# Entity: (?:function|class)\s+([A-Za-z0-9_]+)", it.text)
                if match:
                    fn = f"{path}:{match.group(1)}"
                    if fn not in funcs:
                        funcs.append(fn)

        return RankingResult(files=files[:5], functions=funcs[:5], evidence_ids_cited=cites[:5])


class PipelineRunner:
    """Executes localization pipelines on SWE-bench instances."""

    def __init__(self, config: Dict[str, Any], llm_client: LLMClient):
        self.config = config
        self.llm = llm_client
        self.analyzer = IssueAnalyzer(llm_client)
        self.gate_tau = int(config.get("gate", {}).get("gate_tau", 3))
        self.gate = SufficiencyGate(llm_client, gate_tau=self.gate_tau)
        self.escalation = EscalationPolicy(config, llm_client)

        ret_cfg = config.get("retrieval", {})
        self.code_top_k = int(ret_cfg.get("code_top_k", 5))
        self.doc_top_k = int(ret_cfg.get("doc_top_k", 3))
        self.commit_top_k = int(ret_cfg.get("commit_top_k", 3))
        self.issue_top_k = int(ret_cfg.get("issue_top_k", 3))
        self.token_budget = int(ret_cfg.get("token_budget_per_evidence_set", 4000))
        self.overlap_thresh = float(ret_cfg.get("overlap_audit_threshold", 0.3))

    def run(
        self,
        pipeline_name: str,
        instance: Instance,
        repo_dir: Path,
    ) -> Dict[str, Any]:
        """Runs the chosen pipeline and returns full execution trace."""
        start_time = time.time()
        start_tokens = self.llm.cumulative_prompt_tokens + self.llm.cumulative_completion_tokens
        start_cost = self.llm.cumulative_cost_dollars
        start_calls = self.llm.call_count

        # Build indexes
        code_idx = CodeIndex(repo_dir=repo_dir, base_commit=instance.base_commit)
        doc_idx = DocIndex(repo_dir=repo_dir, base_commit=instance.base_commit)
        commit_idx = CommitIndex(repo_dir=repo_dir, base_commit=instance.base_commit)
        issue_idx = IssueIndex(
            repo=instance.repo,
            instance_created_at=instance.created_at,
            cache_dir=self.config.get("retrieval", {}).get("issue_cache_dir", "cache/issues"),
        )

        available_sources = {"code", "doc", "commit"}
        if issue_idx.available:
            available_sources.add("issue")

        source_indexes = {
            "code": code_idx,
            "doc": doc_idx,
            "commit": commit_idx,
            "issue": issue_idx,
        }

        # Dispatch pipeline
        if pipeline_name == "B_static_bm25":
            result = self._run_B_static(instance, code_idx)
        elif pipeline_name == "C_specrover_style":
            result = self._run_C_specrover(instance, code_idx)
        elif pipeline_name == "E_all_sources_always":
            result = self._run_E_all_sources(instance, source_indexes, available_sources)
        elif pipeline_name == "F_gate_escalation":
            result = self._run_F_gate_escalation(instance, source_indexes, available_sources)
        elif pipeline_name == "G_always_escalate":
            result = self._run_G_always_escalate(instance, source_indexes, available_sources)
        else:
            raise ValueError(f"Unknown pipeline: {pipeline_name}")

        wall_time = time.time() - start_time
        used_tokens = (self.llm.cumulative_prompt_tokens + self.llm.cumulative_completion_tokens) - start_tokens
        used_cost = self.llm.cumulative_cost_dollars - start_cost
        used_calls = self.llm.call_count - start_calls

        # Evidence overlap audit
        ev_set: EvidenceSet = result["evidence_set"]
        flagged_overlap = ev_set.audit_gold_overlap(instance.patch, threshold=self.overlap_thresh)

        # Compute accuracy metrics
        ranking: RankingResult = result["ranking"]
        gold_files = instance.gold_files
        gold_funcs = instance.gold_functions

        acc1 = int(any(f in gold_files for f in ranking.files[:1]))
        acc3 = int(any(f in gold_files for f in ranking.files[:3]))
        acc5 = int(any(f in gold_files for f in ranking.files[:5]))
        matched_gold_files = [f for f in gold_files if f in ranking.files[:5]]
        recall5 = len(matched_gold_files) / max(1, len(gold_files))

        func_acc5 = 0
        if gold_funcs:
            func_acc5 = int(any(fn in gold_funcs for fn in ranking.functions[:5]))

        # Gold file present in retrieved evidence
        gold_in_evidence = any(
            any(gf in item.locator for gf in gold_files)
            for item in ev_set.items()
        )

        trace = {
            "pipeline": pipeline_name,
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "base_commit": instance.base_commit,
            "gold_files": gold_files,
            "gold_functions": gold_funcs,
            "rounds": result["rounds"],
            "total_rounds": len(result["rounds"]),
            "stop_reason": result.get("stop_reason", "completed"),
            "final_ranking": ranking.to_dict(),
            "evidence_count": len(ev_set),
            "evidence_items": [it.to_dict() for it in ev_set.items()],
            "overlap_audit": flagged_overlap,
            "overlap_flagged_count": len(flagged_overlap),
            "gate_stats": result.get("gate_stats", {}),
            "metrics": {
                "file_acc1": acc1,
                "file_acc3": acc3,
                "file_acc5": acc5,
                "file_recall5": recall5,
                "func_acc5": func_acc5,
                "gold_in_evidence": gold_in_evidence,
                "tokens_used": used_tokens,
                "cost_dollars": used_cost,
                "llm_calls": used_calls,
                "wall_time_sec": round(wall_time, 3),
            }
        }
        return trace

    # ================= Pipeline Implementations =================

    def _run_B_static(self, instance: Instance, code_idx: CodeIndex) -> Dict[str, Any]:
        """Pipeline B: Single-shot BM25 over code entities, no gate, no escalation."""
        ev_set = EvidenceSet(token_budget=self.token_budget)
        query = instance.problem_statement[:500]
        items = code_idx.query(query, top_k=self.code_top_k, round_num=1)
        ev_set.add_many(items)

        ranking = rank_candidates(self.llm, instance, None, ev_set)
        rounds = [{
            "round_num": 1,
            "source": "code",
            "query": query,
            "items_retrieved": len(items),
        }]
        return {"evidence_set": ev_set, "ranking": ranking, "rounds": rounds}

    def _run_C_specrover(self, instance: Instance, code_idx: CodeIndex) -> Dict[str, Any]:
        """Pipeline C: Simplified SpecRover-style code-only iterative retrieval with sufficiency judgment."""
        analysis = self.analyzer.analyze(instance.problem_statement)
        ev_set = EvidenceSet(token_budget=self.token_budget)
        max_rounds = int(self.config.get("gate", {}).get("max_rounds", 3))

        rounds = []
        stop_reason = "max_rounds_reached"

        curr_query = analysis.to_query()
        for r in range(1, max_rounds + 1):
            items = code_idx.query(curr_query, top_k=self.code_top_k, round_num=r)
            ev_set.add_many(items)

            # Simple code-only sufficiency check (SpecRover style: is current context enough?)
            prompt = (
                f"Given the issue:\n{instance.problem_statement[:1500]}\n\n"
                f"And current retrieved code evidence:\n{ev_set.pack_for_prompt()[0]}\n\n"
                f"Is this code evidence sufficient to locate and fix the bug? Answer JSON: {{\"sufficient\": true/false, \"next_query\": \"...\"}}"
            )
            res = self.llm.complete(prompt=prompt, json_schema={"type": "object"})
            is_suff = False
            next_q = curr_query
            try:
                data = json.loads(res.text.strip().strip("```json").strip("```"))
                is_suff = bool(data.get("sufficient", False))
                next_q = str(data.get("next_query", curr_query))
            except Exception:
                is_suff = False

            rounds.append({
                "round_num": r,
                "source": "code",
                "query": curr_query,
                "items_retrieved": len(items),
                "sufficient": is_suff,
            })

            if is_suff:
                stop_reason = "gate_passed"
                break
            curr_query = next_q

        ranking = rank_candidates(self.llm, instance, analysis, ev_set)
        return {"evidence_set": ev_set, "ranking": ranking, "rounds": rounds, "stop_reason": stop_reason}

    def _run_E_all_sources(
        self,
        instance: Instance,
        source_indexes: Dict[str, Any],
        available_sources: Set[str],
    ) -> Dict[str, Any]:
        """Pipeline E: Retrieve from all sources simultaneously in one round without gating."""
        analysis = self.analyzer.analyze(instance.problem_statement)
        ev_set = EvidenceSet(token_budget=self.token_budget)
        query = analysis.to_query()

        rounds = []
        for src in ["code", "doc", "issue", "commit"]:
            if src in available_sources:
                idx = source_indexes[src]
                top_k = getattr(self, f"{src}_top_k", 3)
                items = idx.query(query, top_k=top_k, round_num=1)
                ev_set.add_many(items)
                rounds.append({
                    "round_num": 1,
                    "source": src,
                    "query": query,
                    "items_retrieved": len(items),
                })

        ranking = rank_candidates(self.llm, instance, analysis, ev_set)
        return {"evidence_set": ev_set, "ranking": ranking, "rounds": rounds, "stop_reason": "single_round_all"}

    def _run_F_gate_escalation(
        self,
        instance: Instance,
        source_indexes: Dict[str, Any],
        available_sources: Set[str],
    ) -> Dict[str, Any]:
        """Pipeline F (EvidRover): Seed with code, evaluate sufficiency gate, escalate deterministically."""
        analysis = self.analyzer.analyze(instance.problem_statement)
        ev_set = EvidenceSet(token_budget=self.token_budget)

        used_sources: Set[str] = set()
        rounds = []
        gate_stats = []
        stop_reason = "max_rounds_reached"

        # Round 1: Code retrieval
        curr_source = "code"
        curr_query = analysis.to_query()
        round_num = 1
        max_rounds = int(self.config.get("gate", {}).get("max_rounds", 3))

        while round_num <= max_rounds:
            used_sources.add(curr_source)
            idx = source_indexes[curr_source]
            top_k = getattr(self, f"{curr_source}_top_k", 3)
            items = idx.query(curr_query, top_k=top_k, round_num=round_num)
            ev_set.add_many(items)

            # Evaluate Sufficiency Gate
            verdict = self.gate.evaluate(analysis, ev_set, round_num=round_num)
            gate_stats.append(verdict.to_dict())

            decision = self.escalation.decide(
                round_num=round_num,
                gate_verdict=verdict,
                used_sources=used_sources,
                entities=analysis.code_entities,
                available_sources=available_sources,
            )

            rounds.append({
                "round_num": round_num,
                "source": curr_source,
                "query": curr_query,
                "items_retrieved": len(items),
                "gate_verdict": verdict.to_dict(),
                "escalation_decision": decision.to_dict(),
            })

            if not decision.should_escalate:
                stop_reason = decision.stop_reason or "gate_passed"
                break

            curr_source = decision.source
            curr_query = decision.query
            round_num += 1

        ranking = rank_candidates(self.llm, instance, analysis, ev_set)
        return {
            "evidence_set": ev_set,
            "ranking": ranking,
            "rounds": rounds,
            "stop_reason": stop_reason,
            "gate_stats": gate_stats,
        }

    def _run_G_always_escalate(
        self,
        instance: Instance,
        source_indexes: Dict[str, Any],
        available_sources: Set[str],
    ) -> Dict[str, Any]:
        """Pipeline G: Sequential ablation of the gate (escalates through sources round-by-round ignoring gate)."""
        analysis = self.analyzer.analyze(instance.problem_statement)
        ev_set = EvidenceSet(token_budget=self.token_budget)

        sources_sequence = ["code", "doc", "issue", "commit"]
        valid_sequence = [s for s in sources_sequence if s in available_sources]
        rounds = []

        curr_query = analysis.to_query()
        for r, src in enumerate(valid_sequence, start=1):
            idx = source_indexes[src]
            top_k = getattr(self, f"{src}_top_k", 3)
            items = idx.query(curr_query, top_k=top_k, round_num=r)
            ev_set.add_many(items)
            rounds.append({
                "round_num": r,
                "source": src,
                "query": curr_query,
                "items_retrieved": len(items),
            })
            # Query refinement for next source
            curr_query = f"{analysis.symptom[:80]} {src} {' '.join(analysis.code_entities[:2])}"

        ranking = rank_candidates(self.llm, instance, analysis, ev_set)
        return {"evidence_set": ev_set, "ranking": ranking, "rounds": rounds, "stop_reason": "all_sources_visited"}
