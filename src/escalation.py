"""Escalation Policy: rule-based source selection and LLM query refinement."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Set

from src.gate import GateVerdict
from src.llm import LLMClient

logger = logging.getLogger("evidrover.escalation")


@dataclass
class EscalationDecision:
    """Decision on whether to escalate and which source/query to pursue."""
    should_escalate: bool
    source: Optional[str]          # 'code', 'doc', 'commit', 'issue'
    query: str
    stop_reason: Optional[str]     # 'gate_passed', 'max_rounds_reached', 'all_sources_exhausted', 'budget_spent'
    gap_type: str
    round_num: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


QUERY_REFINEMENT_PROMPT = """You are a retrieval query generator for software engineering bug localization.
Based on the identified gap in evidence:
Gap: {gap}
Context Entities: {entities}
Target Source: {source}

Write a concise, targeted search query (2 to 6 keywords) to find this information in {source}.
Output ONLY the query string, nothing else.
"""


class EscalationPolicy:
    """Deterministic, inspectable rule-based escalation policy."""

    def __init__(self, config: Dict[str, Any], llm_client: LLMClient):
        self.llm = llm_client
        esc_cfg = config.get("escalation", {})
        self.rules: Dict[str, List[str]] = esc_cfg.get("policy_rules", {
            "intended_behavior": ["doc", "issue"],
            "similar_failure": ["issue"],
            "history_rationale": ["commit"],
            "missing_definition": ["code"],
        })
        self.max_rounds = int(config.get("gate", {}).get("max_rounds", 3))

    def decide(
        self,
        round_num: int,
        gate_verdict: GateVerdict,
        used_sources: Set[str],
        entities: List[str],
        available_sources: Set[str],
    ) -> EscalationDecision:
        """Determines the next escalation step."""
        # 1. Check if gate passed
        if gate_verdict.passed:
            return EscalationDecision(
                should_escalate=False,
                source=None,
                query="",
                stop_reason="gate_passed",
                gap_type=gate_verdict.gap_type,
                round_num=round_num,
            )

        # 2. Check max rounds
        if round_num >= self.max_rounds:
            return EscalationDecision(
                should_escalate=False,
                source=None,
                query="",
                stop_reason="max_rounds_reached",
                gap_type=gate_verdict.gap_type,
                round_num=round_num,
            )

        # 3. Lookup source candidates by gap_type
        gap_type = gate_verdict.gap_type
        candidate_sources = self.rules.get(gap_type, ["code", "doc", "issue", "commit"])

        # Pick first candidate source that hasn't been used yet and is available
        selected_source = None
        for src in candidate_sources:
            if src not in used_sources and src in available_sources:
                selected_source = src
                break

        # If all rule candidates were used, try any other unused available source
        if not selected_source:
            for fallback_src in ["doc", "issue", "commit", "code"]:
                if fallback_src not in used_sources and fallback_src in available_sources:
                    selected_source = fallback_src
                    break

        # If no unused source remains
        if not selected_source:
            # If code can be queried again with a refined query:
            if "code" in available_sources and round_num < self.max_rounds:
                selected_source = "code"
            else:
                return EscalationDecision(
                    should_escalate=False,
                    source=None,
                    query="",
                    stop_reason="all_sources_exhausted",
                    gap_type=gap_type,
                    round_num=round_num,
                )

        # 4. Generate refined query via LLM
        refined_query = self._refine_query(gate_verdict.gap, entities, selected_source)

        return EscalationDecision(
            should_escalate=True,
            source=selected_source,
            query=refined_query,
            stop_reason=None,
            gap_type=gap_type,
            round_num=round_num + 1,
        )

    def _refine_query(self, gap: str, entities: List[str], source: str) -> str:
        prompt = QUERY_REFINEMENT_PROMPT.format(
            gap=gap,
            entities=", ".join(entities[:3]) if entities else "none",
            source=source,
        )
        try:
            res = self.llm.complete(prompt=prompt)
            query = res.text.strip().strip('"').strip("'")
            return query if query else f"{gap} {' '.join(entities[:2])}"
        except Exception as e:
            logger.warning(f"Failed to refine query via LLM ({e}); using gap text.")
            return f"{gap} {' '.join(entities[:2])}"
