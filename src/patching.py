"""Tier 2: Patch generation module with cited rationale claims and git apply --check validation."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.data import Instance
from src.evidence import EvidenceSet
from src.llm import LLMClient

logger = logging.getLogger("evidrover.patching")


@dataclass
class Claim:
    """An individual assertion made by the patch rationale."""
    claim: str
    cites: List[str]  # e.g., ['E1', 'E3'] or ['none']

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PatchCandidate:
    """Generated candidate patch with structured rationale claims and git applicability."""
    instance_id: str
    diff: str
    claims: List[Claim]
    applies_cleanly: bool
    git_apply_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "diff": self.diff,
            "claims": [c.to_dict() for c in self.claims],
            "applies_cleanly": self.applies_cleanly,
            "git_apply_error": self.git_apply_error,
        }


PATCH_SYSTEM_PROMPT = """You are an automated software repair engineer.
Given a problem statement and a set of verified evidence items (with IDs [E1], [E2], ...),
generate a unified diff patch to fix the bug, accompanied by explicit rationale claims citing the evidence items.

Output strict JSON with this exact schema:
{
  "diff": "--- a/path/to/file.py\\n+++ b/path/to/file.py\\n@@ ... @@\\n-old\\n+new\\n",
  "claims": [
    {
      "claim": "Clear technical assertion explaining what this change does and why",
      "cites": ["E1", "E2"]
    }
  ]
}
Each claim MUST cite the evidence IDs that justify it (e.g. ["E1"]), or ["none"] if unbacked.
Only output valid JSON, nothing else.
"""


class PatchGenerator:
    """Generates candidate patches citing retrieved evidence."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def generate_patch(
        self,
        instance: Instance,
        evidence_set: EvidenceSet,
        repo_dir: Optional[Path] = None,
    ) -> PatchCandidate:
        ev_prompt, packed_items = evidence_set.pack_for_prompt()

        prompt = (
            f"=== PROBLEM STATEMENT ===\n{instance.problem_statement[:3000]}\n\n"
            f"=== RETRIEVED EVIDENCE ===\n{ev_prompt}\n\n"
            f"Generate the fix patch unified diff and rationale claims in strict JSON format."
        )

        res = self.llm.complete(
            prompt=prompt,
            system=PATCH_SYSTEM_PROMPT,
            json_schema={"type": "object"},
        )

        diff = ""
        claims: List[Claim] = []

        try:
            cleaned = res.text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            data = json.loads(cleaned.strip())
            diff = data.get("diff", "")
            raw_claims = data.get("claims", [])
            for rc in raw_claims:
                claims.append(Claim(
                    claim=str(rc.get("claim", "")),
                    cites=[str(c) for c in rc.get("cites", ["none"])],
                ))
        except Exception as e:
            logger.warning(f"Failed to parse patch JSON ({e}); using fallback patch.")
            diff = f"# Failed to parse LLM patch\n"
            claims.append(Claim(claim="Unparsable patch output", cites=["none"]))

        # Validate git apply --check at base_commit
        applies_cleanly, err_msg = self.validate_diff_applies(diff, repo_dir)

        return PatchCandidate(
            instance_id=instance.instance_id,
            diff=diff,
            claims=claims,
            applies_cleanly=applies_cleanly,
            git_apply_error=err_msg,
        )

    def validate_diff_applies(
        self, diff_text: str, repo_dir: Optional[Path]
    ) -> tuple[bool, Optional[str]]:
        """Run `git apply --check` against the repository at base_commit."""
        if not diff_text or not diff_text.strip() or not repo_dir or not repo_dir.exists():
            return False, "Empty diff or repo directory not available"

        with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False, encoding="utf-8") as tf:
            tf.write(diff_text)
            diff_file = tf.name

        try:
            cmd = ["git", "apply", "--check", "--ignore-whitespace", diff_file]
            proc = subprocess.run(cmd, cwd=str(repo_dir), capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                return True, None
            else:
                return False, proc.stderr.strip()
        except Exception as e:
            return False, str(e)
        finally:
            try:
                Path(diff_file).unlink()
            except Exception:
                pass
