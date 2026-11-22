"""LLM Client module for EvidRover-Lite.

Supports:
- Multiple providers: 'mock', 'gemini', 'openai_compatible', 'anthropic'.
- Persistent disk caching keyed by SHA256 of call parameters.
- Hard token and dollar budget enforcement.
- Automatic retries with exponential backoff.
- Cost calculation based on config pricing table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

logger = logging.getLogger("evidrover.llm")


class BudgetExceededError(RuntimeError):
    """Raised when spending or token budget is exceeded."""
    pass


@dataclass
class LLMResult:
    """Standardized result returned by LLMClient.complete()."""
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost_dollars: float
    cached: bool
    prompt_hash: str
    provider: str
    model: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LLMClient:
    """Provider-agnostic LLM client with caching and budget enforcement."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        llm_cfg = config.get("llm", {})
        self.provider = (llm_cfg.get("provider") or os.environ.get("LLM_PROVIDER") or "mock").lower()
        self.model = llm_cfg.get("model") or os.environ.get("LLM_MODEL") or "gemini-2.5-flash"
        self.temperature = float(llm_cfg.get("temperature", 0.0))
        self.max_retries = int(llm_cfg.get("max_retries", 2))

        # Budget caps
        budget_cfg = llm_cfg.get("budget", {})
        self.max_tokens = int(budget_cfg.get("max_tokens", 5_000_000))
        self.max_cost_dollars = float(budget_cfg.get("max_cost_dollars", 10.0))

        # Cumulative spending
        self.cumulative_prompt_tokens = 0
        self.cumulative_completion_tokens = 0
        self.cumulative_cost_dollars = 0.0
        self.call_count = 0

        # Pricing table
        self.pricing = llm_cfg.get("pricing", {})

        # Disk cache
        self.cache_dir = Path(llm_cfg.get("cache_dir", "cache/llm"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Provider initialization
        self._init_provider()

    def _init_provider(self) -> None:
        """Initialize provider SDK clients."""
        if self.provider == "gemini":
            try:
                from google import genai
                cfg_key = self.config.get("llm", {}).get("api_key", "")
                api_key = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or cfg_key
                if not api_key:
                    logger.warning("No Gemini API key found. Falling back to mock provider.")
                    self.provider = "mock"
                else:
                    self._gemini_client = genai.Client(
                        api_key=api_key,
                        http_options={"api_version": "v1beta"}
                    )
            except Exception as e:
                logger.warning(f"Failed to initialize Gemini client ({e}). Falling back to mock.")
                self.provider = "mock"
        elif self.provider == "openai_compatible":
            try:
                import openai
                api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "dummy")
                base_url = os.environ.get("LLM_BASE_URL", None)
                self._openai_client = openai.OpenAI(api_key=api_key, base_url=base_url)
            except Exception as e:
                logger.warning(f"Failed to initialize OpenAI client ({e}). Falling back to mock.")
                self.provider = "mock"

    def compute_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Compute dollar cost based on model pricing table in config."""
        model_prices = self.pricing.get(self.model, self.pricing.get("default", {}))
        input_rate = float(model_prices.get("input_cost_per_million", 0.0))
        output_rate = float(model_prices.get("output_cost_per_million", 0.0))
        return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000.0

    def _hash_call(self, prompt: str, system: Optional[str], json_schema: Optional[Dict[str, Any]]) -> str:
        payload = f"{self.provider}|{self.model}|{self.temperature}|{system or ''}|{json.dumps(json_schema, sort_keys=True) if json_schema else ''}|{prompt}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        json_schema: Optional[Dict[str, Any]] = None,
    ) -> LLMResult:
        """Execute completion with caching, retries, and budget tracking."""
        prompt_hash = self._hash_call(prompt, system, json_schema)
        cache_path = self.cache_dir / f"{prompt_hash}.json"

        # Check disk cache
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return LLMResult(
                    text=data["text"],
                    prompt_tokens=data.get("prompt_tokens", 0),
                    completion_tokens=data.get("completion_tokens", 0),
                    cost_dollars=data.get("cost_dollars", 0.0),
                    cached=True,
                    prompt_hash=prompt_hash,
                    provider=self.provider,
                    model=self.model,
                )
            except Exception as e:
                logger.warning(f"Failed to read cache {cache_path}: {e}")

        # Check budget limits before calling live API
        if (self.cumulative_prompt_tokens + self.cumulative_completion_tokens) >= self.max_tokens:
            raise BudgetExceededError(
                f"Token budget exceeded: {self.cumulative_prompt_tokens + self.cumulative_completion_tokens} >= {self.max_tokens}"
            )
        if self.cumulative_cost_dollars >= self.max_cost_dollars:
            raise BudgetExceededError(
                f"Cost budget exceeded: ${self.cumulative_cost_dollars:.4f} >= ${self.max_cost_dollars:.4f}"
            )

        # Call provider with retries
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                text, p_tokens, c_tokens = self._call_provider(prompt, system, json_schema)
                cost = self.compute_cost(p_tokens, c_tokens)

                # Update cumulative usage
                self.cumulative_prompt_tokens += p_tokens
                self.cumulative_completion_tokens += c_tokens
                self.cumulative_cost_dollars += cost
                self.call_count += 1

                result = LLMResult(
                    text=text,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    cost_dollars=cost,
                    cached=False,
                    prompt_hash=prompt_hash,
                    provider=self.provider,
                    model=self.model,
                )

                # Save to disk cache
                try:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(result.to_dict(), f, indent=2)
                except Exception as e:
                    logger.warning(f"Failed to write cache {cache_path}: {e}")

                return result
            except BudgetExceededError:
                raise
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                logger.warning(f"LLM call attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries:
                    if "429" in err_str or "quota" in err_str or "resource_exhausted" in err_str:
                        logger.info("Rate limit (429) hit. Pausing 9 seconds before retry...")
                        time.sleep(9.0)
                    else:
                        time.sleep(1.5 ** attempt)

        # If live provider failed after retries, fail loudly to prevent silent substitution
        if self.provider != "mock":
            raise RuntimeError(f"Live provider {self.provider} failed after retries: {last_error}")

        logger.error(f"Provider {self.provider} failed all retries ({last_error}). Falling back to mock response.")
        text, p_tokens, c_tokens = self._mock_complete(prompt, system, json_schema)
        cost = 0.0
        result = LLMResult(
            text=text,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            cost_dollars=cost,
            cached=False,
            prompt_hash=prompt_hash,
            provider="mock",
            model="mock-fallback",
        )
        return result

    def _call_provider(
        self, prompt: str, system: Optional[str], json_schema: Optional[Dict[str, Any]]
    ) -> tuple[str, int, int]:
        if self.provider == "mock":
            return self._mock_complete(prompt, system, json_schema)
        elif self.provider == "gemini":
            return self._call_gemini(prompt, system, json_schema)
        elif self.provider == "openai_compatible":
            return self._call_openai(prompt, system, json_schema)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def _call_gemini(
        self, prompt: str, system: Optional[str], json_schema: Optional[Dict[str, Any]]
    ) -> tuple[str, int, int]:
        contents = prompt
        config_kwargs: Dict[str, Any] = {"temperature": self.temperature}
        if system:
            config_kwargs["system_instruction"] = system
        if json_schema:
            config_kwargs["response_mime_type"] = "application/json"

        response = self._gemini_client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config_kwargs,
        )
        text = response.text or ""
        p_tokens = getattr(response.usage_metadata, "prompt_token_count", len(prompt) // 4)
        c_tokens = getattr(response.usage_metadata, "candidates_token_count", len(text) // 4)
        return text, p_tokens, c_tokens

    def _call_openai(
        self, prompt: str, system: Optional[str], json_schema: Optional[Dict[str, Any]]
    ) -> tuple[str, int, int]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if json_schema:
            kwargs["response_format"] = {"type": "json_object"}

        res = self._openai_client.chat.completions.create(**kwargs)
        text = res.choices[0].message.content or ""
        p_tokens = res.usage.prompt_tokens if res.usage else len(prompt) // 4
        c_tokens = res.usage.completion_tokens if res.usage else len(text) // 4
        return text, p_tokens, c_tokens

    def _mock_complete(
        self, prompt: str, system: Optional[str], json_schema: Optional[Dict[str, Any]]
    ) -> tuple[str, int, int]:
        """High-fidelity deterministic mock responses for testing and offline execution."""
        p_lower = (prompt + " " + (system or "")).lower()

        # Simulated token counts
        p_tokens = max(20, len(prompt) // 4)

        # 1. Issue Analyzer
        if "issue analyzer" in p_lower or "symptom" in p_lower or "information_needs" in p_lower:
            # Extract possible words from prompt to make mock dynamic
            words = [w for w in re.findall(r"[a-zA-Z_]{4,}", prompt) if w not in {"issue", "problem", "statement", "please", "return"}]
            entity1 = words[0] if words else "target_func"
            entity2 = words[1] if len(words) > 1 else "HandlerClass"
            data = {
                "symptom": f"Failure during execution involving {entity1}",
                "expected_behavior": f"Should correctly execute {entity1} without error",
                "code_entities": [entity1, entity2],
                "stack_frames": [f"{entity1}.py:42 in run"],
                "information_needs": [f"Definition and usage of {entity1}", f"Expected behavior of {entity2}"],
            }
            text = json.dumps(data)

        # 2. Sufficiency Gate
        elif "sufficiency gate" in p_lower or "relevance" in p_lower and "completeness" in p_lower:
            # Determine sufficiency based on whether code evidence is present
            has_gold = "evidence_gold" in p_lower or "target" in p_lower
            if "round 2" in p_lower or "round 3" in p_lower or has_gold:
                data = {
                    "relevance": 5,
                    "completeness": 5,
                    "consistency": 4,
                    "sufficient": True,
                    "gap": "none",
                    "gap_type": "none",
                    "evidence_ids_used": ["E1", "E2"],
                }
            else:
                data = {
                    "relevance": 4,
                    "completeness": 2,
                    "consistency": 4,
                    "sufficient": False,
                    "gap": "Missing expected behavior specification and documentation",
                    "gap_type": "intended_behavior",
                    "evidence_ids_used": ["E1"],
                }
            text = json.dumps(data)

        # 3. Escalation Query Refinement
        elif "refine query" in p_lower or "escalation query" in p_lower:
            text = "behavior specification and usage examples"

        # 4. Final Ranking
        elif "rank" in p_lower and ("candidate files" in p_lower or "candidate functions" in p_lower):
            # Parse evidence items to find filenames
            locators = re.findall(r"\[(E\d+)\]\s+locator:\s*([^\s\n]+)", prompt)
            files = []
            funcs = []
            cites = []
            for eid, loc in locators:
                cites.append(eid)
                filepath = loc.split(":")[0] if ":" in loc else loc
                if filepath not in files and (filepath.endswith(".py") or "/" in filepath):
                    files.append(filepath)
                    funcs.append(f"{filepath}:process")
            if not files:
                files = ["core/handler.py", "utils/helpers.py"]
                funcs = ["core/handler.py:handle", "utils/helpers.py:clean"]
                cites = ["E1"]
            data = {
                "files": files[:5],
                "functions": funcs[:5],
                "evidence_ids_cited": cites[:3],
            }
            text = json.dumps(data)

        # 5. Patching (Tier 2)
        elif "tier 2 patch" in p_lower or "unified diff" in p_lower:
            data = {
                "diff": "--- a/core/handler.py\n+++ b/core/handler.py\n@@ -10,3 +10,3 @@\n-    return None\n+    return True\n",
                "claims": [
                    {"claim": "Return True when handler succeeds instead of None", "cites": ["E1"]},
                    {"claim": "Preserves backward compatibility", "cites": ["E2"]},
                ],
            }
            text = json.dumps(data)

        # 6. Verifier (Tier 2)
        elif "claim verifier" in p_lower or "supported | contradicted | unsupported" in p_lower:
            data = {
                "verdict": "Supported",
                "explanation": "The cited evidence document clearly mentions this expectation.",
            }
            text = json.dumps(data)

        # Default JSON or text
        elif json_schema:
            text = json.dumps({"status": "ok", "message": "mock completed"})
        else:
            text = "Mock completion successful."

        c_tokens = max(10, len(text) // 4)
        return text, p_tokens, c_tokens
