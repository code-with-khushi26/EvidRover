"""Documentation index: parses authored documentation at base_commit split by section headings."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from rank_bm25 import BM25Okapi

from src.evidence import Evidence
from src.indexes.base import BaseIndex
from src.indexes.code import tokenize

logger = logging.getLogger("evidrover.indexes.doc")


class DocIndex(BaseIndex):
    """Indexes markdown, restructured text, and text documentation in the repository."""

    def __init__(self, repo_dir: Path, base_commit: Optional[str] = None):
        self.repo_dir = repo_dir
        self.base_commit = base_commit
        self.sections: List[Dict[str, Any]] = []
        self.corpus_tokens: List[List[str]] = []
        self.bm25: Optional[BM25Okapi] = None
        self._build_index()

    def _build_index(self) -> None:
        if not self.repo_dir.exists():
            return

        doc_extensions = {".md", ".rst", ".txt"}
        doc_files = []
        for ext in doc_extensions:
            doc_files.extend(self.repo_dir.rglob(f"*{ext}"))

        for doc_path in doc_files:
            parts = doc_path.parts
            if any(p.startswith(".") or p in {"venv", ".venv", "tests"} for p in parts[:-1]):
                continue

            rel_path = str(doc_path.relative_to(self.repo_dir)).replace("\\", "/")
            try:
                content = doc_path.read_text(encoding="utf-8", errors="ignore")
                file_sections = self._split_into_sections(rel_path, content)
                for sec in file_sections:
                    tokens = tokenize(f"{rel_path} {sec['heading']} {sec['text']}")
                    if tokens:
                        self.sections.append(sec)
                        self.corpus_tokens.append(tokens)
            except Exception as e:
                logger.debug(f"Failed to read doc {rel_path}: {e}")

        if self.corpus_tokens:
            self.bm25 = BM25Okapi(self.corpus_tokens)
            logger.info(f"Indexed {len(self.sections)} documentation sections in {self.repo_dir.name}.")

    def _split_into_sections(self, filepath: str, content: str) -> List[Dict[str, Any]]:
        """Split markdown/rst into sections by heading."""
        lines = content.splitlines()
        sections = []
        current_heading = "Top"
        current_lines: List[str] = []
        start_line = 1

        for idx, line in enumerate(lines, start=1):
            # Markdown heading or RST underline
            is_md_header = line.startswith("#")
            is_rst_header = (
                idx < len(lines)
                and len(lines[idx]) >= 3
                and all(c in "=-~`" for c in lines[idx])
                and len(lines[idx]) >= len(line.strip())
            )

            if (is_md_header or is_rst_header) and current_lines:
                sec_text = "\n".join(current_lines).strip()
                if sec_text:
                    sections.append({
                        "filepath": filepath,
                        "heading": current_heading,
                        "start_line": start_line,
                        "end_line": idx - 1,
                        "text": sec_text[:1500],  # cap section length
                    })
                current_heading = line.lstrip("# ").strip()
                current_lines = [line]
                start_line = idx
            else:
                current_lines.append(line)

        if current_lines:
            sec_text = "\n".join(current_lines).strip()
            if sec_text:
                sections.append({
                    "filepath": filepath,
                    "heading": current_heading,
                    "start_line": start_line,
                    "end_line": len(lines),
                    "text": sec_text[:1500],
                })

        return sections

    def query(self, query_text: str, top_k: int = 3, round_num: int = 1) -> List[Evidence]:
        if not self.bm25 or not self.sections:
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
            item = self.sections[idx]
            results.append(Evidence(
                id="",
                source="doc",
                locator=f"{item['filepath']}:{item['start_line']}-{item['end_line']}",
                text=f"# Doc: {item['filepath']} > {item['heading']}\n{item['text']}",
                score=float(scores[idx]),
                retrieved_by_round=round_num,
            ))
        return results
