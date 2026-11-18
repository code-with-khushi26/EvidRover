"""Base index interface for multi-source retrieval."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from src.evidence import Evidence


class BaseIndex(ABC):
    """Abstract interface implemented by all source indexes."""

    @abstractmethod
    def query(self, query_text: str, top_k: int = 5, round_num: int = 1) -> List[Evidence]:
        """Retrieve top_k Evidence items for a given query string."""
        pass
