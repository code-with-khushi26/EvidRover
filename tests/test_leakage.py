"""Leakage protocol test suite.

Proves:
1. No commit after base_commit is retrievable.
2. Synthetic future canary commit inserted into git history is NEVER returned.
3. No issue created after instance.created_at is retrievable.
4. Fix PR and any issue referencing the fix PR are strictly excluded.
"""

import json
import shutil
import tempfile
from pathlib import Path
import pytest
import git

from src.indexes.commit import CommitIndex
from src.indexes.issue import IssueIndex


@pytest.fixture
def synthetic_repo():
    """Create a temporary git repository with a base commit and a future canary commit."""
    temp_dir = tempfile.mkdtemp()
    repo_path = Path(temp_dir)
    repo = git.Repo.init(str(repo_path))

    # Commit 1: Initial commit
    f1 = repo_path / "main.py"
    f1.write_text("def solve(): return 1\n", encoding="utf-8")
    repo.index.add(["main.py"])
    c1 = repo.index.commit("Initial base commit before fix")

    # Commit 2 (Canary): A later commit that fixes the issue (leakage canary!)
    f1.write_text("def solve(): return 42  # CANARY_LEAKAGE_SECRET\n", encoding="utf-8")
    repo.index.add(["main.py"])
    c2 = repo.index.commit("Fix PR commit CANARY_LEAKAGE_SECRET")

    yield repo_path, c1.hexsha, c2.hexsha
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_canary_commit_not_retrievable(synthetic_repo):
    """Canary test: synthetic future commit must NEVER be returned when indexing at base_commit."""
    repo_path, base_sha, canary_sha = synthetic_repo

    commit_index = CommitIndex(repo_dir=repo_path, base_commit=base_sha)
    results = commit_index.query("CANARY_LEAKAGE_SECRET", top_k=5)

    # Must find nothing containing the canary secret
    assert len(results) == 0, "Leakage detected! Canary commit was retrievable!"

    # Ensure canary commit SHA is not present anywhere in the indexed commits
    indexed_shas = [c["hexsha"] for c in commit_index.commits]
    assert canary_sha not in indexed_shas, f"Canary SHA {canary_sha} leaked into commit index!"


def test_no_commits_after_base_commit(synthetic_repo):
    repo_path, base_sha, _ = synthetic_repo
    commit_index = CommitIndex(repo_dir=repo_path, base_commit=base_sha)

    # Base commit itself should be indexed
    assert any(c["hexsha"] == base_sha for c in commit_index.commits)
    # The indexed commits must be exactly 1 (the base commit and its ancestors)
    assert len(commit_index.commits) == 1


def test_issue_temporal_leakage_filter(tmp_path):
    """Ensure issues created after instance_created_at are excluded."""
    issues_data = [
        {"number": 101, "title": "Old bug", "body": "Reported before fix", "created_at": "2023-01-01T10:00:00Z"},
        {"number": 102, "title": "Future bug", "body": "Reported after fix", "created_at": "2023-05-01T10:00:00Z"},
    ]
    cache_file = tmp_path / "test_repo__test_issues.json"
    cache_file.write_text(json.dumps(issues_data), encoding="utf-8")

    index = IssueIndex(
        repo="test_repo/test",
        instance_created_at="2023-03-01T00:00:00Z",
        fix_pr_number="999",
        cache_dir=str(tmp_path),
    )

    indexed_numbers = [item["number"] for item in index.items]
    assert "101" in indexed_numbers
    assert "102" not in indexed_numbers, "Future issue leaked into index!"
    assert index.items_excluded_future == 1


def test_issue_fix_pr_leakage_filter(tmp_path):
    """Ensure the fix PR and issues mentioning the fix PR number are excluded."""
    issues_data = [
        {"number": 200, "title": "Normal issue", "body": "Nothing special", "created_at": "2022-01-01T00:00:00Z"},
        {"number": 555, "title": "The Fix PR itself", "body": "Fixes bug", "created_at": "2022-01-01T00:00:00Z"},
        {"number": 201, "title": "Related", "body": "See fix in PR #555 for details", "created_at": "2022-01-01T00:00:00Z"},
    ]
    cache_file = tmp_path / "owner__project_issues.json"
    cache_file.write_text(json.dumps(issues_data), encoding="utf-8")

    index = IssueIndex(
        repo="owner/project",
        instance_created_at="2023-01-01T00:00:00Z",
        fix_pr_number="555",
        cache_dir=str(tmp_path),
    )

    indexed_numbers = [item["number"] for item in index.items]
    assert "200" in indexed_numbers
    assert "555" not in indexed_numbers, "Fix PR leaked into index!"
    assert "201" not in indexed_numbers, "Issue referencing Fix PR leaked into index!"
    assert index.items_excluded_fix_pr == 2
