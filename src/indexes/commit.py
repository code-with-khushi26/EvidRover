"""Commit history index: git log and blame reachable strictly from base_commit."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import git
from rank_bm25 import BM25Okapi

from src.evidence import Evidence
from src.indexes.base import BaseIndex
from src.indexes.code import tokenize

logger = logging.getLogger("evidrover.indexes.commit")


class CommitIndex(BaseIndex):
    """Indexes git commit history reachable strictly from base_commit."""

    def __init__(self, repo_dir: Path, base_commit: str, max_commits: int = 100):
        self.repo_dir = repo_dir
        self.base_commit = base_commit
        self.max_commits = max_commits
        self.commits: List[Dict[str, Any]] = []
        self.corpus_tokens: List[List[str]] = []
        self.bm25: Optional[BM25Okapi] = None
        self._build_index()

    def _build_index(self) -> None:
        if not self.repo_dir.exists() or not (self.repo_dir / ".git").exists():
            return

        try:
            repo = git.Repo(str(self.repo_dir))
            # Strict leakage check: iter_commits starts AT base_commit, strictly reachable ancestors only
            for c in repo.iter_commits(self.base_commit, max_count=self.max_commits):
                # Diffstat / changed files
                changed_files = list(c.stats.files.keys()) if hasattr(c, "stats") else []
                summary = c.summary
                body = c.message.strip()
                files_str = " ".join(changed_files)
                full_text = f"{summary}\n{body}\nChanged files: {files_str}"

                tokens = tokenize(f"{c.hexsha[:8]} {summary} {body} {files_str}")
                if tokens:
                    self.commits.append({
                        "hexsha": c.hexsha,
                        "short_sha": c.hexsha[:8],
                        "summary": summary,
                        "text": full_text,
                        "date": c.committed_datetime.isoformat(),
                        "author": str(c.author),
                        "changed_files": changed_files,
                    })
                    self.corpus_tokens.append(tokens)

            if self.corpus_tokens:
                self.bm25 = BM25Okapi(self.corpus_tokens)
                logger.info(f"Indexed {len(self.commits)} commits reachable from {self.base_commit[:8]}.")
        except Exception as e:
            logger.warning(f"Failed to build commit index for {self.repo_dir.name} at {self.base_commit}: {e}")

    def query(self, query_text: str, top_k: int = 3, round_num: int = 1) -> List[Evidence]:
        if not self.bm25 or not self.commits:
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
            item = self.commits[idx]
            results.append(Evidence(
                id="",
                source="commit",
                locator=f"commit:{item['short_sha']}",
                text=f"# Commit {item['short_sha']} ({item['date']}) by {item['author']}\n{item['text'][:1200]}",
                created_at=item["date"],
                score=float(scores[idx]),
                retrieved_by_round=round_num,
            ))
        return results

    def get_blame_evidence(self, filepath: str, start_line: int, end_line: int) -> List[Evidence]:
        """Git blame evidence for a file and line range strictly at base_commit."""
        try:
            repo = git.Repo(str(self.repo_dir))
            blame_entries = repo.blame(self.base_commit, filepath, L=f"{start_line},{end_line}")
            results = []
            for commit, lines in blame_entries:
                snippet = "".join(lines)[:500]
                results.append(Evidence(
                    id="",
                    source="commit",
                    locator=f"blame:{filepath}:{start_line}-{end_line}@{commit.hexsha[:8]}",
                    text=f"# Blame {commit.hexsha[:8]}: {commit.summary}\n{snippet}",
                    created_at=commit.committed_datetime.isoformat(),
                    score=5.0,
                ))
            return results
        except Exception as e:
            logger.debug(f"Blame failed for {filepath}: {e}")
            return []
