"""Sufficiency Gate module: LLM judge scoring evidence sufficiency and naming missing gaps."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from src.analyzer import IssueAnalysis
from src.evidence import EvidenceSet
from src.llm import LLMClient

logger = logging.getLogger("evidrover.gate")

VALID_GAP_TYPES = {
    "intended_behavior",
    "similar_failure",
    "history_rationale",
    "missing_definition",
    "none",
}


@dataclass
class GateVerdict:
    """Evaluation result produced by the sufficiency gate."""
    relevance: int                 # 1 - 5
    completeness: int              # 1 - 5
    consistency: int               # 1 - 5
    sufficient: bool               # Raw model verdict
    passed: bool                   # True iff sufficient AND min(scores) >= gate_tau
    gap: str                       # Plain-language description of missing information
    gap_type: str                  # Categorical gap type
    evidence_ids_used: List[str]   # Citing evidence items considered

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


GATE_SYSTEM_PROMPT = """You are a rigorous software engineering judge evaluating whether retrieved evidence is sufficient to localize and resolve a bug.

Given the issue analysis and the retrieved evidence items (with IDs), score the evidence and identify any remaining gaps.

Scoring criteria (integer 1 to 5):
- relevance: Does the evidence directly pertain to the error and mentioned entities? (1=irrelevant, 5=exact match)
- completeness: Does the evidence provide enough context to localize the exact faulty file and method? (1=wholly inadequate, 5=complete)
- consistency: Is the evidence consistent and uncontradicted across sources? (1=major conflict, 5=fully coherent)

Respond strictly in valid JSON matching this schema:
{
  "relevance": 1,
  "completeness": 1,
  "consistency": 1,
  "sufficient": false,
  "gap": "Concise description of the missing information (or 'none' if sufficient)",
  "gap_type": "intended_behavior | similar_failure | history_rationale | missing_definition | none",
  "evidence_ids_used": ["E1", "E2"]
}
Only choose gap_type from: "intended_behavior", "similar_failure", "history_rationale", "missing_definition", "none".
"""


class SufficiencyGate:
    """Evaluates whether the collected evidence set is sufficient for bug localization."""

    def __init__(self, llm_client: LLMClient, gate_tau: int = 3):
        self.llm = llm_client
        self.gate_tau = gate_tau

    def evaluate(self, analysis: IssueAnalysis, evidence_set: EvidenceSet, round_num: int = 1) -> GateVerdict:
        """Run sufficiency gate check on the current evidence set."""
        ev_prompt, packed_items = evidence_set.pack_for_prompt()

        prompt = (
            f"=== ISSUE ANALYSIS ===\n"
            f"Symptom: {analysis.symptom}\n"
            f"Expected: {analysis.expected_behavior}\n"
            f"Entities: {', '.join(analysis.code_entities)}\n"
            f"Needs: {'; '.join(analysis.information_needs)}\n\n"
            f"=== RETRIEVED EVIDENCE (Round {round_num}) ===\n"
            f"{ev_prompt}\n\n"
            f"Evaluate sufficiency and produce the JSON verdict:"
        )

        # Attempt 1
        res = self.llm.complete(
            prompt=prompt,
            system=GATE_SYSTEM_PROMPT,
            json_schema={"type": "object"},
        )

        verdict = self._parse_and_validate(res.text)
        if verdict is None:
            # Repair attempt: retry once with explicit repair prompt
            logger.warning("Gate JSON malformed. Attempting repair retry.")
            repair_prompt = f"The following response was not valid JSON:\n{res.text}\nPlease fix and output only the valid JSON verdict matching schema."
            res_repair = self.llm.complete(prompt=repair_prompt, system=GATE_SYSTEM_PROMPT)
            verdict = self._parse_and_validate(res_repair.text)

        if verdict is None:
            # Safe conservative fallback: mark insufficient
            logger.error("Gate repair failed; using fallback verdict.")
            verdict = GateVerdict(
                relevance=3,
                completeness=2,
                consistency=3,
                sufficient=False,
                passed=False,
                gap="Failed to parse gate verdict; conservative escalation.",
                gap_type="missing_definition",
                evidence_ids_used=[],
            )

        return verdict

    def _parse_and_validate(self, text: str) -> Optional[GateVerdict]:
        try:
            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            data = json.loads(cleaned)
            relevance = int(data.get("relevance", 3))
            completeness = int(data.get("completeness", 3))
            consistency = int(data.get("consistency", 3))
            sufficient = bool(data.get("sufficient", False))
            gap = str(data.get("gap", "none"))
            gap_type = str(data.get("gap_type", "missing_definition")).strip().lower()

            if gap_type not in VALID_GAP_TYPES:
                gap_type = "missing_definition"

            evidence_ids = data.get("evidence_ids_used", [])
            if not isinstance(evidence_ids, list):
                evidence_ids = []

            # Passed iff sufficient AND min(scores) >= gate_tau
            passed = sufficient and min(relevance, completeness, consistency) >= self.gate_tau

            return GateVerdict(
                relevance=relevance,
                completeness=completeness,
                consistency=consistency,
                sufficient=sufficient,
                passed=passed,
                gap=gap,
                gap_type=gap_type,
                evidence_ids_used=[str(e) for e in evidence_ids],
            )
        except Exception as e:
            logger.debug(f"JSON validation failed: {e}")
            return None
