"""Unit tests for gold patch parsing and localization label extraction."""

import json
from pathlib import Path
import pytest
import yaml

from src.data import parse_gold_patch, DatasetManager


SAMPLE_PATCH = """diff --git a/requests/sessions.py b/requests/sessions.py
--- a/requests/sessions.py
+++ b/requests/sessions.py
@@ -168,8 +168,11 @@ def resolve_redirects(self, resp, req, stream=False, timeout=None,
             if new_auth is not None:
                 prepared_request.prepare_auth(new_auth)
"""

def test_parse_gold_patch():
    files, funcs = parse_gold_patch(SAMPLE_PATCH)
    assert files == ["requests/sessions.py"]
    assert "requests/sessions.py:resolve_redirects" in funcs


def test_dataset_subsets_non_overlapping():
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    dm = DatasetManager(cfg)
    subset_ids, pilot_ids = dm.generate_subsets()
    assert len(subset_ids) == 40
    assert len(pilot_ids) == 10
    overlap = set(subset_ids).intersection(set(pilot_ids))
    assert len(overlap) == 0, f"Overlap found between subset and pilot: {overlap}"
