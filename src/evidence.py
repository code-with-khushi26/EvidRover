"""Evidence item representation and EvidenceSet manager."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Set


@dataclass
class Evidence:
    """A discrete unit of evidence retrieved from repository sources."""
    id: str                         # Unique ID within the instance, e.g. 'E1', 'E2'
    source: str                     # 'code', 'doc', 'commit', 'issue'
    locator: str                    # e.g., 'src/models.py:10-45', 'issue#1242', 'commit:a1b2c3d'
    text: str                       # Raw excerpt
    created_at: Optional[str] = None  # ISO timestamp or commit date
    score: float = 0.0             # Retrieval relevance score (BM25 or similarity)
    retrieved_by_round: int = 1     # Escalation round in which this item was fetched

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Evidence:
        return cls(**data)

    def estimated_tokens(self) -> int:
        """Rough estimation: 1 token ~ 4 characters."""
        return max(1, len(self.text) // 4)


class EvidenceSet:
    """Manages collection, deduplication, and token packing of evidence."""

    def __init__(self, token_budget: int = 4000):
        self.token_budget = token_budget
        self._items: List[Evidence] = []
        self._seen_locators: Set[str] = set()

    def add(self, item: Evidence) -> bool:
        """Add item if locator has not been seen. Assigns canonical ID if needed."""
        key = f"{item.source}:{item.locator}"
        if key in self._seen_locators:
            return False
        self._seen_locators.add(key)
        item.id = f"E{len(self._items) + 1}"
        self._items.append(item)
        return True

    def add_many(self, items: List[Evidence]) -> int:
        added = 0
        for it in items:
            if self.add(it):
                added += 1
        return added

    def items(self) -> List[Evidence]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def pack_for_prompt(self, max_tokens: Optional[int] = None) -> tuple[str, List[Evidence]]:
        """Pack evidence items up to token budget.
        
        Returns:
            prompt_str: Formatted string listing items with their IDs and locators.
            packed_items: List of Evidence items included within the budget.
        """
        budget = max_tokens or self.token_budget
        used_tokens = 0
        packed = []
        lines = []

        for item in self._items:
            item_tokens = item.estimated_tokens() + 20  # account for header
            if used_tokens + item_tokens > budget and packed:
                break
            packed.append(item)
            used_tokens += item_tokens
            lines.append(
                f"[{item.id}] source: {item.source} | locator: {item.locator}\n"
                f"```\n{item.text.strip()}\n```\n"
            )

        prompt_str = "\n".join(lines) if lines else "(No evidence collected yet)"
        return prompt_str, packed

    def audit_gold_overlap(self, gold_patch: str, threshold: float = 0.3) -> List[Dict[str, Any]]:
        """Compute token overlap between each evidence item and the gold patch.
        
        Used solely for leakage analysis. Items exceeding threshold are flagged.
        """
        if not gold_patch:
            return []

        # Tokenize patch (extracting identifiers, words)
        patch_tokens = set(re.findall(r"[A-Za-z0-9_]{3,}", gold_patch.lower()))
        if not patch_tokens:
            return []

        flagged = []
        for item in self._items:
            item_tokens = set(re.findall(r"[A-Za-z0-9_]{3,}", item.text.lower()))
            if not item_tokens:
                continue
            intersection = patch_tokens.intersection(item_tokens)
            overlap_ratio = len(intersection) / float(len(item_tokens))
            if overlap_ratio >= threshold:
                flagged.append({
                    "evidence_id": item.id,
                    "source": item.source,
                    "locator": item.locator,
                    "overlap_ratio": round(overlap_ratio, 4),
                    "common_tokens_count": len(intersection)
                })
        return flagged
