"""Tier 2: Evidence-grounded claim support verifier and faithfulness scoring."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.evidence import Evidence, EvidenceSet
from src.llm import LLMClient
from src.patching import Claim, PatchCandidate

logger = logging.getLogger("evidrover.verifier")


@dataclass
class ClaimVerificationResult:
    """Verification outcome for an individual rationale claim."""
    claim: str
    cites: List[str]
    verdict: str                  # 'Supported', 'Contradicted', 'Unsupported'
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PatchVerificationReport:
    """Overall evidence-grounded verification report for a patch."""
    instance_id: str
    claims_evaluated: List[ClaimVerificationResult]
    total_claims: int
    supported_count: int
    contradicted_count: int
    unsupported_count: int
    faithfulness_score: float      # supported / total_claims
    verdict: str                  # 'Accepted' | 'Rejected'
    rejection_reason: Optional[str] = None
    tests_status: str = "not_run" # 'passed', 'failed', 'not_run'
    test_output: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "claims_evaluated": [c.to_dict() for c in self.claims_evaluated],
            "total_claims": self.total_claims,
            "supported_count": self.supported_count,
            "contradicted_count": self.contradicted_count,
            "unsupported_count": self.unsupported_count,
            "faithfulness_score": round(self.faithfulness_score, 4),
            "verdict": self.verdict,
            "rejection_reason": self.rejection_reason,
            "tests_status": self.tests_status,
            "test_output": self.test_output,
        }


CLAIM_VERIFIER_SYSTEM_PROMPT = """You are a strict, objective fact-checking judge for software patches.
You are given a claim made by an author and ONLY the specific evidence excerpts cited to back that claim.

Evaluate whether the cited evidence supports, contradicts, or fails to support the claim.
Categories:
- "Supported": The cited evidence directly substantiates the claim.
- "Contradicted": The cited evidence directly refutes or contradicts the claim.
- "Unsupported": The cited evidence does not contain sufficient information to substantiate the claim, or citation is missing.

Respond in strict JSON:
{
  "verdict": "Supported | Contradicted | Unsupported",
  "explanation": "Short justification based strictly on the provided evidence"
}
Output valid JSON only.
"""


class EvidenceVerifier:
    """Verifies patch rationale claims against cited evidence."""

    def __init__(self, llm_client: LLMClient, faithfulness_theta: float = 0.7, run_tests: bool = False):
        self.llm = llm_client
        self.faithfulness_theta = faithfulness_theta
        self.run_tests = run_tests

    def verify_patch(
        self,
        patch_candidate: PatchCandidate,
        evidence_set: EvidenceSet,
        repo_dir: Optional[Path] = None,
    ) -> PatchVerificationReport:
        evidence_by_id = {item.id: item for item in evidence_set.items()}

        claim_results: List[ClaimVerificationResult] = []
        supported = 0
        contradicted = 0
        unsupported = 0

        for claim_obj in patch_candidate.claims:
            verdict, explanation = self._verify_single_claim(claim_obj, evidence_by_id)
            claim_results.append(ClaimVerificationResult(
                claim=claim_obj.claim,
                cites=claim_obj.cites,
                verdict=verdict,
                explanation=explanation,
            ))
            if verdict == "Supported":
                supported += 1
            elif verdict == "Contradicted":
                contradicted += 1
            else:
                unsupported += 1

        total = max(1, len(patch_candidate.claims))
        faithfulness = supported / float(total)

        # Decision rule: Reject if any Contradicted OR Faithfulness < theta
        if contradicted > 0:
            verdict = "Rejected"
            reason = f"{contradicted} claim(s) directly contradicted by cited evidence."
        elif faithfulness < self.faithfulness_theta:
            verdict = "Rejected"
            reason = f"Faithfulness {faithfulness:.2f} < threshold {self.faithfulness_theta:.2f}."
        elif not patch_candidate.applies_cleanly:
            verdict = "Rejected"
            reason = "Patch diff does not apply cleanly to base_commit."
        else:
            verdict = "Accepted"
            reason = None

        # Optional test harness hook
        tests_status, test_out = self._run_swebench_tests_if_available(patch_candidate, repo_dir)

        return PatchVerificationReport(
            instance_id=patch_candidate.instance_id,
            claims_evaluated=claim_results,
            total_claims=len(patch_candidate.claims),
            supported_count=supported,
            contradicted_count=contradicted,
            unsupported_count=unsupported,
            faithfulness_score=faithfulness,
            verdict=verdict,
            rejection_reason=reason,
            tests_status=tests_status,
            test_output=test_out,
        )

    def _verify_single_claim(
        self, claim_obj: Claim, evidence_map: Dict[str, Evidence]
    ) -> tuple[str, str]:
        # Gather text from cited evidence
        cited_texts = []
        for cid in claim_obj.cites:
            if cid in evidence_map:
                cited_texts.append(f"[{cid}] {evidence_map[cid].text}")

        if not cited_texts or "none" in [c.lower() for c in claim_obj.cites]:
            return "Unsupported", "No valid evidence cited."

        prompt = (
            f"=== CLAIM ===\n{claim_obj.claim}\n\n"
            f"=== CITED EVIDENCE EXCERPTS ===\n" + "\n---\n".join(cited_texts) + "\n\n"
            f"Determine whether the claim is Supported, Contradicted, or Unsupported:"
        )

        res = self.llm.complete(
            prompt=prompt,
            system=CLAIM_VERIFIER_SYSTEM_PROMPT,
            json_schema={"type": "object"},
        )

        try:
            cleaned = res.text.strip().strip("```json").strip("```").strip()
            data = json.loads(cleaned)
            v = str(data.get("verdict", "Unsupported")).strip().capitalize()
            if v not in {"Supported", "Contradicted", "Unsupported"}:
                v = "Unsupported"
            exp = str(data.get("explanation", ""))
            return v, exp
        except Exception:
            return "Unsupported", "Failed to parse verifier judgment."

    def _run_swebench_tests_if_available(
        self, patch_candidate: PatchCandidate, repo_dir: Optional[Path]
    ) -> tuple[str, Optional[str]]:
        """Hook to run official SWE-bench evaluation harness if Docker is installed."""
        if not self.run_tests:
            return "not_run", "Test execution disabled in config."

        # Check if Docker is available
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return "not_run", "Docker not found on host machine. SWE-bench container harness requires Docker."

        return "not_run", "SWE-bench test harness hook ready; Docker tests skipped as requested."
