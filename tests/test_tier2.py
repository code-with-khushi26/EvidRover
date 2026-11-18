"""Unit tests for Tier 2 patching, rationale claim extraction, and verification."""

import tempfile
from pathlib import Path
import pytest
import yaml

from src.evidence import Evidence, EvidenceSet
from src.data import Instance
from src.llm import LLMClient
from src.patching import Claim, PatchCandidate, PatchGenerator
from src.verifier import EvidenceVerifier


@pytest.fixture
def mock_llm_client():
    cfg = {
        "llm": {
            "provider": "mock",
            "model": "mock-model",
            "temperature": 0.0,
            "cache_dir": "cache/test_llm",
        }
    }
    return LLMClient(cfg)


def test_patch_generation_and_claims(mock_llm_client):
    generator = PatchGenerator(mock_llm_client)
    inst = Instance(
        instance_id="test_inst_1",
        repo="test/repo",
        base_commit="abc1234",
        problem_statement="Fix error in handler",
        patch="",
        created_at="2023-01-01T00:00:00Z",
        gold_files=["core/handler.py"],
        gold_functions=["core/handler.py:handle"],
    )
    ev_set = EvidenceSet()
    ev_set.add(Evidence(id="E1", source="code", locator="core/handler.py:1-10", text="def handle(): return None"))
    
    patch_cand = generator.generate_patch(inst, ev_set)
    assert isinstance(patch_cand, PatchCandidate)
    assert len(patch_cand.claims) >= 1
    assert patch_cand.claims[0].claim != ""


def test_evidence_verifier(mock_llm_client):
    verifier = EvidenceVerifier(mock_llm_client, faithfulness_theta=0.5)
    ev_set = EvidenceSet()
    ev_set.add(Evidence(id="E1", source="doc", locator="docs/guide.md:1-5", text="Handlers must return True upon success."))

    cand = PatchCandidate(
        instance_id="test_inst_1",
        diff="--- a/f.py\n+++ b/f.py\n",
        claims=[
            Claim(claim="Handlers must return True on success", cites=["E1"]),
            Claim(claim="Unsupported assertion", cites=["none"]),
        ],
        applies_cleanly=True,
    )

    report = verifier.verify_patch(cand, ev_set)
    assert report.total_claims == 2
    assert report.supported_count >= 1
    assert 0.0 <= report.faithfulness_score <= 1.0
