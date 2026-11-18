"""Code index: AST-based indexing and BM25 search over repository source files."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from rank_bm25 import BM25Okapi

from src.evidence import Evidence
from src.indexes.base import BaseIndex

logger = logging.getLogger("evidrover.indexes.code")


def tokenize(text: str) -> List[str]:
    """Tokenize code/text splitting by punctuation, camelCase, and snake_case."""
    # Split camelCase and identifiers
    tokens = re.findall(r"[A-Za-z0-9]+", text.lower())
    return [t for t in tokens if len(t) > 1]


class CodeIndex(BaseIndex):
    """Indexes Python functions, methods, and classes using AST and BM25."""

    def __init__(self, repo_dir: Path, base_commit: Optional[str] = None):
        self.repo_dir = repo_dir
        self.base_commit = base_commit
        self.entities: List[Dict[str, Any]] = []
        self.corpus_tokens: List[List[str]] = []
        self.bm25: Optional[BM25Okapi] = None
        self._build_index()

    def _build_index(self) -> None:
        """Walk repo directory at base_commit and extract Python AST entities."""
        if not self.repo_dir.exists():
            logger.warning(f"Repo dir {self.repo_dir} does not exist. Index will be empty.")
            return

        py_files = list(self.repo_dir.rglob("*.py"))
        for py_path in py_files:
            # Skip hidden dirs, virtualenvs, git
            parts = py_path.parts
            if any(p.startswith(".") or p in {"venv", ".venv", "tests", "test"} for p in parts[:-1]):
                continue

            rel_path = str(py_path.relative_to(self.repo_dir)).replace("\\", "/")
            try:
                content = py_path.read_text(encoding="utf-8", errors="ignore")
                lines = content.splitlines()
                tree = ast.parse(content, filename=rel_path)

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        start_line = getattr(node, "lineno", 1)
                        end_line = getattr(node, "end_lineno", len(lines))
                        docstring = ast.get_docstring(node) or ""
                        snippet = "\n".join(lines[start_line - 1 : end_line])

                        # Cap snippet length for index to avoid massive evidence
                        snippet_capped = "\n".join(lines[start_line - 1 : min(end_line, start_line + 60)])

                        entity_tokens = tokenize(f"{rel_path} {node.name} {docstring} {snippet_capped}")
                        self.entities.append({
                            "type": "class" if isinstance(node, ast.ClassDef) else "function",
                            "name": node.name,
                            "filepath": rel_path,
                            "start_line": start_line,
                            "end_line": end_line,
                            "docstring": docstring,
                            "snippet": snippet_capped,
                        })
                        self.corpus_tokens.append(entity_tokens)
            except Exception as e:
                logger.debug(f"Failed to parse {rel_path}: {e}")

        if self.corpus_tokens:
            self.bm25 = BM25Okapi(self.corpus_tokens)
            logger.info(f"Indexed {len(self.entities)} code entities in {self.repo_dir.name}.")

    def query(self, query_text: str, top_k: int = 5, round_num: int = 1) -> List[Evidence]:
        """BM25 search over code entities."""
        if not self.bm25 or not self.entities:
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
            item = self.entities[idx]
            results.append(Evidence(
                id="",
                source="code",
                locator=f"{item['filepath']}:{item['start_line']}-{item['end_line']}",
                text=f"# Entity: {item['type']} {item['name']}\n{item['snippet']}",
                score=float(scores[idx]),
                retrieved_by_round=round_num,
            ))
        return results

    def search_class(self, class_name: str) -> List[Evidence]:
        """AutoCodeRover style tool: exact or fuzzy class search."""
        results = []
        target = class_name.lower()
        for item in self.entities:
            if item["type"] == "class" and item["name"].lower() == target:
                results.append(Evidence(
                    id="",
                    source="code",
                    locator=f"{item['filepath']}:{item['start_line']}-{item['end_line']}",
                    text=f"# Class: {item['name']}\n{item['snippet']}",
                    score=10.0,
                ))
        return results

    def search_method(self, method_name: str) -> List[Evidence]:
        """AutoCodeRover style tool: exact or fuzzy method/function search."""
        results = []
        target = method_name.lower()
        for item in self.entities:
            if item["type"] == "function" and item["name"].lower() == target:
                results.append(Evidence(
                    id="",
                    source="code",
                    locator=f"{item['filepath']}:{item['start_line']}-{item['end_line']}",
                    text=f"# Function: {item['name']}\n{item['snippet']}",
                    score=10.0,
                ))
        return results

    def search_code(self, keyword: str, top_k: int = 5) -> List[Evidence]:
        """AutoCodeRover style tool: keyword search over codebase."""
        return self.query(keyword, top_k=top_k)
