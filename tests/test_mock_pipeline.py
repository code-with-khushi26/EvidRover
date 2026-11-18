"""Unit tests for LLM client, cache, budget caps, and mock end-to-end execution."""

import json
import shutil
import tempfile
from pathlib import Path
import pytest
import yaml

from src.llm import LLMClient, BudgetExceededError, LLMResult
from src.evidence import Evidence, EvidenceSet


@pytest.fixture
def temp_config():
    temp_dir = tempfile.mkdtemp()
    cfg = {
        "llm": {
            "provider": "mock",
            "model": "mock-model",
            "temperature": 0.0,
            "cache_dir": str(Path(temp_dir) / "cache" / "llm"),
            "max_retries": 1,
            "budget": {
                "max_tokens": 1000,
                "max_cost_dollars": 0.05,
            },
            "pricing": {
                "mock-model": {
                    "input_cost_per_million": 1.0,
                    "output_cost_per_million": 2.0,
                }
            }
        }
    }
    yield cfg
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_llm_mock_completion(temp_config):
    client = LLMClient(temp_config)
    res = client.complete("Issue Analyzer: Please analyze the problem symptom")
    assert isinstance(res, LLMResult)
    assert not res.cached
    data = json.loads(res.text)
    assert "symptom" in data
    assert "code_entities" in data


def test_llm_cache(temp_config):
    client = LLMClient(temp_config)
    prompt = "Rank candidate files for repository"
    res1 = client.complete(prompt)
    assert not res1.cached
    
    # Second call should be served from disk cache
    res2 = client.complete(prompt)
    assert res2.cached
    assert res2.prompt_hash == res1.prompt_hash
    assert res2.text == res1.text


def test_llm_budget_cap(temp_config):
    # Set a tiny budget
    temp_config["llm"]["budget"]["max_tokens"] = 50
    client = LLMClient(temp_config)
    client.cumulative_prompt_tokens = 60
    with pytest.raises(BudgetExceededError):
        client.complete("A prompt that exceeds tokens")


def test_evidence_set_and_packing():
    ev_set = EvidenceSet(token_budget=100)
    e1 = Evidence(id="", source="code", locator="app.py:1-10", text="def foo():\n    return 42", score=1.5)
    e2 = Evidence(id="", source="code", locator="app.py:1-10", text="duplicate locator", score=1.0)
    e3 = Evidence(id="", source="doc", locator="README.md:1-5", text="# Documentation\nInfo here", score=0.8)

    assert ev_set.add(e1) is True
    assert ev_set.add(e2) is False  # deduplicated
    assert ev_set.add(e3) is True
    assert len(ev_set) == 2

    prompt_str, packed = ev_set.pack_for_prompt()
    assert len(packed) == 2
    assert "[E1]" in prompt_str
    assert "[E2]" in prompt_str


def test_overlap_audit():
    ev_set = EvidenceSet()
    ev_set.add(Evidence(id="E1", source="code", locator="f.py:1-5", text="def calculate_total(items): return sum(items)"))
    patch = "@@ -1 +1 @@\n-def calculate_total(items):\n+def calculate_total(items, tax=0):"
    flagged = ev_set.audit_gold_overlap(patch, threshold=0.2)
    assert len(flagged) >= 1
    assert flagged[0]["evidence_id"] == "E1"
