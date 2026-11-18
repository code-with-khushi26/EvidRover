"""Issue Analyzer module: parses problem statement into structured information needs."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from src.llm import LLMClient

logger = logging.getLogger("evidrover.analyzer")


@dataclass
class IssueAnalysis:
    """Structured representation of an issue problem statement."""
    symptom: str
    expected_behavior: str
    code_entities: List[str]
    stack_frames: List[str]
    information_needs: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> IssueAnalysis:
        return cls(
            symptom=data.get("symptom", ""),
            expected_behavior=data.get("expected_behavior", ""),
            code_entities=data.get("code_entities", []) or [],
            stack_frames=data.get("stack_frames", []) or [],
            information_needs=data.get("information_needs", []) or [],
        )

    def to_query(self) -> str:
        """Combine entities, symptom, and needs into an effective search query."""
        parts = []
        if self.code_entities:
            parts.extend(self.code_entities[:3])
        if self.symptom:
            parts.append(self.symptom[:150])
        if self.expected_behavior:
            parts.append(self.expected_behavior[:100])
        return " ".join(parts)


ANALYZER_SYSTEM_PROMPT = """You are an expert software engineering issue analyzer.
Analyze the given GitHub issue problem statement and output strict JSON with exactly these keys:
{
  "symptom": "concise description of the observed bug or failure",
  "expected_behavior": "what should happen instead",
  "code_entities": ["list", "of", "function_names", "class_names", "module_paths", "error_types"],
  "stack_frames": ["list", "of", "files_or_locations", "from_traceback"],
  "information_needs": ["short questions or items needed to locate and fix the bug"]
}
Output valid JSON only. Do not include markdown code fences or conversational text.
"""


class IssueAnalyzer:
    """Invokes LLM to decompose a problem statement into structured needs."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def analyze(self, problem_statement: str) -> IssueAnalysis:
        prompt = (
            f"Analyze the following GitHub issue problem statement:\n\n"
            f"```\n{problem_statement[:4000]}\n```\n\n"
            f"Produce the structured JSON analysis."
        )

        res = self.llm.complete(
            prompt=prompt,
            system=ANALYZER_SYSTEM_PROMPT,
            json_schema={"type": "object"},
        )

        try:
            cleaned = self._clean_json(res.text)
            data = json.loads(cleaned)
            return IssueAnalysis.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to parse Analyzer JSON ({e}). Text: {res.text[:150]}")
            # Fallback heuristic extraction
            words = re.findall(r"[A-Za-z0-9_]{3,}", problem_statement)
            entities = [w for w in words if "_" in w or (w[0].isupper() and len(w) > 3)][:5]
            return IssueAnalysis(
                symptom=problem_statement[:200],
                expected_behavior="",
                code_entities=entities,
                stack_frames=[],
                information_needs=["Locate faulty function or class"],
            )

    @staticmethod
    def _clean_json(text: str) -> str:
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()
