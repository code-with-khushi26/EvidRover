"""Git repository manager: cloning, checkout at base_commit, and git helpers."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import git

logger = logging.getLogger("evidrover.repos")


class RepoManager:
    """Manages cloning repositories and ensuring clean checkout at base_commit."""

    def __init__(self, config: Dict[str, Any]):
        self.repos_dir = Path(config.get("data", {}).get("repos_dir", "workdir/repos"))
        self.repos_dir.mkdir(parents=True, exist_ok=True)

    def get_repo_path(self, repo_name: str) -> Path:
        """Returns local path for repo (e.g. workdir/repos/psf/requests)."""
        safe_name = repo_name.replace("/", os.sep)
        return self.repos_dir / safe_name

    def ensure_repo(self, repo_name: str) -> Path:
        """Clones repository if not present."""
        repo_path = self.get_repo_path(repo_name)
        if repo_path.exists() and (repo_path / ".git").exists():
            return repo_path

        repo_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/{repo_name}.git"
        logger.info(f"Cloning {url} into {repo_path}...")
        try:
            # Run git clone with timeout to avoid hanging indefinitely on massive repos
            subprocess.run(
                ["git", "clone", url, str(repo_path)],
                check=True,
                capture_output=True,
                timeout=45,
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"Git clone timed out for {url} after 45s. Falling back.")
            raise RuntimeError(f"Clone timed out for {repo_name}")
        except Exception as e:
            logger.error(f"Failed to clone {url}: {e}")
            raise
        return repo_path

    def checkout_base_commit(self, repo_name: str, base_commit: str) -> Path:
        """Checks out the exact base_commit, cleaning working tree."""
        repo_path = self.ensure_repo(repo_name)
        repo = git.Repo(str(repo_path))

        # Check out base commit cleanly
        logger.info(f"Checking out {repo_name} at base_commit: {base_commit}")
        repo.git.clean("-fd")
        repo.git.checkout("-f", base_commit)
        return repo_path

    def get_commit_log(
        self, repo_path: Path, base_commit: str, max_count: int = 50
    ) -> List[Dict[str, Any]]:
        """Get git log commits reachable strictly from base_commit."""
        repo = git.Repo(str(repo_path))
        commits = []
        for c in repo.iter_commits(base_commit, max_count=max_count):
            commits.append({
                "hexsha": c.hexsha,
                "message": c.message.strip(),
                "author": str(c.author),
                "committed_datetime": c.committed_datetime.isoformat(),
                "summary": c.summary,
            })
        return commits

    def get_blame(
        self, repo_path: Path, filepath: str, start_line: int, end_line: int, base_commit: str
    ) -> List[Dict[str, Any]]:
        """Run git blame on a file/line range strictly at base_commit."""
        repo = git.Repo(str(repo_path))
        try:
            blame_entries = repo.blame(base_commit, filepath, L=f"{start_line},{end_line}")
            result = []
            for commit, lines in blame_entries:
                result.append({
                    "commit": commit.hexsha[:8],
                    "message": commit.summary,
                    "date": commit.committed_datetime.isoformat(),
                    "lines": lines,
                })
            return result
        except Exception as e:
            logger.warning(f"Git blame failed for {filepath} at {base_commit}: {e}")
            return []
