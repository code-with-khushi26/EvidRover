"""Data loading, stratification, and gold localization label extraction."""

from __future__ import annotations

import ast
import json
import logging
import random
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import unidiff

logger = logging.getLogger("evidrover.data")


@dataclass
class Instance:
    """A SWE-bench Lite instance with precomputed gold localization labels."""
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    patch: str
    created_at: str
    gold_files: List[str]
    gold_functions: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Instance:
        return cls(**data)


def parse_gold_patch(patch_text: str, repo_dir: Optional[Path] = None) -> Tuple[List[str], List[str]]:
    """Extract gold modified files and functions from unified diff.
    
    Uses `unidiff` to extract modified file paths and changed line ranges.
    Uses Python `ast` on source files at base_commit (if repo_dir is provided)
    to map changed lines to enclosing functions/classes, with fallback to hunk headers.
    """
    if not patch_text or not patch_text.strip():
        return [], []

    patch_set = None
    try:
        patch_set = unidiff.PatchSet(patch_text)
    except Exception as e:
        logger.debug(f"Unidiff parse exception ({e}), falling back to regex diff parsing.")

    gold_files: List[str] = []
    gold_functions: List[str] = []

    if patch_set is None:
        # Fallback regex extraction
        for line in patch_text.splitlines():
            if line.startswith("diff --git a/"):
                parts = line.split(" b/")
                if len(parts) == 2:
                    filepath = parts[1].strip()
                    if filepath not in gold_files:
                        gold_files.append(filepath)
            elif line.startswith("--- a/"):
                filepath = line[6:].strip()
                if filepath not in gold_files:
                    gold_files.append(filepath)
            elif line.startswith("+++ b/"):
                filepath = line[6:].strip()
                if filepath not in gold_files:
                    gold_files.append(filepath)
            elif line.startswith("@@"):
                match = re.search(r"@@\s*(?:def\s+|class\s+)?([A-Za-z0-9_]+)", line)
                if match:
                    func_name = match.group(1)
                    curr_file = gold_files[-1] if gold_files else "unknown.py"
                    item = f"{curr_file}:{func_name}"
                    if item not in gold_functions:
                        gold_functions.append(item)
        return sorted(gold_files), sorted(gold_functions)

    for patched_file in patch_set:
        filepath = patched_file.path
        if filepath.startswith("a/") or filepath.startswith("b/"):
            filepath = filepath[2:]
        if filepath not in gold_files:
            gold_files.append(filepath)

        # Collect changed line numbers in the target file
        changed_lines: Set[int] = set()
        for hunk in patched_file:
            # Fallback 1: hunk section header (e.g. @@ ... @@ def func_name)
            sec_header = getattr(hunk, "section_header", "")
            if sec_header and sec_header.strip():
                sec = sec_header.strip()
                # Often contains "def func_name" or "class ClassName"
                func_match = None
                for keyword in ("def ", "class "):
                    if keyword in sec:
                        name = sec.split(keyword)[-1].split("(")[0].split(":")[0].strip()
                        if name:
                            func_match = f"{filepath}:{name}"
                            break
                if func_match and func_match not in gold_functions:
                    gold_functions.append(func_match)

            for line in hunk:
                if line.is_added or line.is_removed:
                    changed_lines.add(line.source_line_no or line.target_line_no or 0)

        # Precise AST mapping if file exists on disk
        if repo_dir and filepath.endswith(".py"):
            target_path = repo_dir / filepath
            if target_path.exists():
                try:
                    content = target_path.read_text(encoding="utf-8", errors="ignore")
                    tree = ast.parse(content, filename=filepath)
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            start_line = getattr(node, "lineno", 0)
                            end_line = getattr(node, "end_lineno", start_line)
                            # Check overlap with changed lines
                            if any(start_line <= ln <= end_line for ln in changed_lines if ln > 0):
                                fn_name = f"{filepath}:{node.name}"
                                if fn_name not in gold_functions:
                                    gold_functions.append(fn_name)
                except Exception as e:
                    logger.debug(f"AST parse error for {target_path}: {e}")

    return sorted(gold_files), sorted(gold_functions)


class DatasetManager:
    """Manages SWE-bench Lite dataset splits, subset stratification, and loading."""

    def __init__(self, config: Dict[str, Any]):
        data_cfg = config.get("data", {})
        self.dataset_name = data_cfg.get("dataset_name", "princeton-nlp/SWE-bench_Lite")
        self.dataset_split = data_cfg.get("dataset_split", "test")
        self.seed = int(data_cfg.get("seed", 42))
        self.subset_size = int(data_cfg.get("subset_size", 40))
        self.pilot_size = int(data_cfg.get("pilot_size", 10))
        self.repos_dir = Path(data_cfg.get("repos_dir", "workdir/repos"))
        self.cache_dir = Path(data_cfg.get("cache_dir", "cache/data"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path("data")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.subset_path = self.data_dir / "subset.json"
        self.pilot_path = self.data_dir / "pilot.json"
        self._raw_instances: Optional[List[Dict[str, Any]]] = None

    def load_raw_dataset(self) -> List[Dict[str, Any]]:
        """Load SWE-bench Lite dataset, caching raw records locally."""
        if self._raw_instances is not None:
            return self._raw_instances

        cache_file = self.cache_dir / "swe_bench_lite_raw.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    self._raw_instances = json.load(f)
                    return self._raw_instances
            except Exception as e:
                logger.warning(f"Failed to read cached raw dataset: {e}")

        from datasets import load_dataset
        ds = load_dataset(self.dataset_name, split=self.dataset_split)
        instances = []
        for row in ds:
            instances.append({
                "instance_id": row["instance_id"],
                "repo": row["repo"],
                "base_commit": row["base_commit"],
                "problem_statement": row["problem_statement"],
                "patch": row["patch"],
                "created_at": row.get("created_at", ""),
            })

        self._raw_instances = instances
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(instances, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to cache raw dataset: {e}")

        return self._raw_instances

    def generate_subsets(self, force: bool = False) -> Tuple[List[str], List[str]]:
        """Create stratified subset.json and non-overlapping pilot.json."""
        if not force and self.subset_path.exists() and self.pilot_path.exists():
            with open(self.subset_path, "r", encoding="utf-8") as f:
                subset_ids = json.load(f)
            with open(self.pilot_path, "r", encoding="utf-8") as f:
                pilot_ids = json.load(f)
            return subset_ids, pilot_ids

        raw = self.load_raw_dataset()

        # Group instance IDs by repo for stratified sampling
        repo_to_ids: Dict[str, List[str]] = defaultdict(list)
        for row in raw:
            repo_to_ids[row["repo"]].append(row["instance_id"])

        rng = random.Random(self.seed)
        for repo in repo_to_ids:
            rng.shuffle(repo_to_ids[repo])

        # Stratified sampling for subset (default 40)
        subset_ids: List[str] = []
        pilot_ids: List[str] = []

        repos = sorted(repo_to_ids.keys())
        repo_ptrs = {r: 0 for r in repos}

        # First round: take from each repo round-robin for subset
        while len(subset_ids) < self.subset_size:
            progress = False
            for r in repos:
                if repo_ptrs[r] < len(repo_to_ids[r]) and len(subset_ids) < self.subset_size:
                    subset_ids.append(repo_to_ids[r][repo_ptrs[r]])
                    repo_ptrs[r] += 1
                    progress = True
            if not progress:
                break

        # Second round: take from remaining for pilot (default 10)
        while len(pilot_ids) < self.pilot_size:
            progress = False
            for r in repos:
                if repo_ptrs[r] < len(repo_to_ids[r]) and len(pilot_ids) < self.pilot_size:
                    pilot_ids.append(repo_to_ids[r][repo_ptrs[r]])
                    repo_ptrs[r] += 1
                    progress = True
            if not progress:
                break

        subset_ids.sort()
        pilot_ids.sort()

        # Save to data/
        with open(self.subset_path, "w", encoding="utf-8") as f:
            json.dump(subset_ids, f, indent=2)

        with open(self.pilot_path, "w", encoding="utf-8") as f:
            json.dump(pilot_ids, f, indent=2)

        logger.info(f"Generated {len(subset_ids)} subset instances and {len(pilot_ids)} pilot instances.")
        return subset_ids, pilot_ids

    def get_instances(self, instance_ids: List[str]) -> List[Instance]:
        """Load instances with gold labels computed."""
        raw = self.load_raw_dataset()
        id_map = {row["instance_id"]: row for row in raw}

        instances = []
        for iid in instance_ids:
            if iid not in id_map:
                continue
            row = id_map[iid]
            gold_files, gold_funcs = parse_gold_patch(row["patch"])
            inst = Instance(
                instance_id=row["instance_id"],
                repo=row["repo"],
                base_commit=row["base_commit"],
                problem_statement=row["problem_statement"],
                patch=row["patch"],
                created_at=row.get("created_at", ""),
                gold_files=gold_files,
                gold_functions=gold_funcs,
            )
            instances.append(inst)
        return instances
