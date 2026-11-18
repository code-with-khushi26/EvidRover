"""GitHub Issue & PR index with strict temporal and fix-PR leakage filters."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests
from rank_bm25 import BM25Okapi

from src.evidence import Evidence
from src.indexes.base import BaseIndex
from src.indexes.code import tokenize

logger = logging.getLogger("evidrover.indexes.issue")


class IssueIndex(BaseIndex):
    """Indexes GitHub issues and PRs strictly created before instance.created_at."""

    def __init__(
        self,
        repo: str,
        instance_created_at: str,
        fix_pr_number: Optional[str] = None,
        cache_dir: str = "cache/issues",
    ):
        self.repo = repo
        self.instance_created_at = instance_created_at
        self.fix_pr_number = fix_pr_number
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.available: bool = True
        self.unavailability_reason: str = ""
        self.items: List[Dict[str, Any]] = []
        self.corpus_tokens: List[List[str]] = []
        self.bm25: Optional[BM25Okapi] = None

        # Leakage audit stats
        self.items_excluded_future: int = 0
        self.items_excluded_fix_pr: int = 0

        self._build_index()

    def _build_index(self) -> None:
        raw_items = self._fetch_repo_issues()
        if not self.available:
            return

        for item in raw_items:
            # Rule 1: Temporal filter - strictly created_at < instance_created_at
            item_created = item.get("created_at", "")
            if self.instance_created_at and item_created:
                if item_created >= self.instance_created_at:
                    self.items_excluded_future += 1
                    continue

            # Rule 2: Exclude fix PR and any mention of fix PR
            item_num = str(item.get("number", ""))
            item_body = item.get("body") or ""
            item_title = item.get("title") or ""

            if self.fix_pr_number:
                if item_num == str(self.fix_pr_number):
                    self.items_excluded_fix_pr += 1
                    continue
                # Mention of fix PR (e.g. #1234 or PR 1234 or pull/1234)
                pr_str = str(self.fix_pr_number)
                combined = f"{item_title} {item_body}".lower()
                patterns = [
                    rf"#{pr_str}\b",
                    rf"pull/{pr_str}\b",
                    rf"pr\s*#?\s*{pr_str}\b",
                    rf"issue\s*#?\s*{pr_str}\b",
                ]
                if any(re.search(pat, combined) for pat in patterns):
                    self.items_excluded_fix_pr += 1
                    continue

            full_text = f"Title: {item_title}\n{item_body}"
            tokens = tokenize(f"issue {item_num} {item_title} {item_body}")
            if tokens:
                self.items.append({
                    "number": item_num,
                    "title": item_title,
                    "body": item_body,
                    "created_at": item_created,
                    "text": full_text[:1500],
                })
                self.corpus_tokens.append(tokens)

        logger.info(
            f"IssueIndex for {self.repo}: {len(self.items)} valid items indexed. "
            f"Excluded: {self.items_excluded_future} future, {self.items_excluded_fix_pr} fix-PR related."
        )

        if self.corpus_tokens:
            self.bm25 = BM25Okapi(self.corpus_tokens)

    def _fetch_repo_issues(self) -> List[Dict[str, Any]]:
        """Fetch issues/PRs from GitHub API or local disk cache."""
        safe_repo = self.repo.replace("/", "__")
        cache_file = self.cache_dir / f"{safe_repo}_issues.json"

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read issue cache {cache_file}: {e}")

        # Live GitHub API fetch
        token = os.environ.get("GITHUB_TOKEN")
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "EvidRover-Lite",
        }
        if token:
            headers["Authorization"] = f"token {token}"

        url = f"https://api.github.com/repos/{self.repo}/issues?state=all&per_page=100"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 403 or resp.status_code == 429:
                self.available = False
                self.unavailability_reason = "rate_limited"
                logger.warning(f"GitHub API rate limit reached for {self.repo}. Marking source as unavailable.")
                return []
            elif resp.status_code != 200:
                self.available = False
                self.unavailability_reason = f"http_{resp.status_code}"
                return []

            data = resp.json()
            if not isinstance(data, list):
                self.available = False
                self.unavailability_reason = "invalid_response"
                return []

            # Save to disk cache
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to write issue cache: {e}")

            return data
        except Exception as e:
            self.available = False
            self.unavailability_reason = f"network_error: {str(e)[:50]}"
            logger.info(f"Issue source unavailable for {self.repo}: {self.unavailability_reason}")
            return []

    def query(self, query_text: str, top_k: int = 3, round_num: int = 1) -> List[Evidence]:
        if not self.available or not self.bm25 or not self.items:
            return []

        q_tokens = tokenize(query_text)
        if not q_tokens:
            return []

        scores = self.bm25.get_scores(q_tokens)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

        results = []
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            item = self.items[idx]
            results.append(Evidence(
                id="",
                source="issue",
                locator=f"issue#{item['number']}",
                text=f"# Issue #{item['number']}: {item['title']} (created {item['created_at']})\n{item['text']}",
                created_at=item["created_at"],
                score=float(scores[idx]),
                retrieved_by_round=round_num,
            ))
        return results
